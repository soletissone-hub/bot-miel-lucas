import os
import json
import logging
from datetime import datetime
from urllib.parse import quote

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler,
)

# ─────────────────────────────────────────────
#  CONFIGURACIÓN
#  TELEGRAM_TOKEN y GOOGLE_CREDENTIALS_JSON viven solo como variables de
#  entorno (en Railway) — nunca se escriben en este archivo para que no
#  terminen en el repo de GitHub.
# ─────────────────────────────────────────────
TELEGRAM_TOKEN          = os.environ.get("TELEGRAM_TOKEN", "")
SPREADSHEET_ID          = os.environ.get("SPREADSHEET_ID", "19psQEs7UHpEa1SJFfeyy1UnxkVtE1Ay6yj0pd9Ug7Lk")
BRAND_NAME              = os.environ.get("BRAND_NAME", "Miel Lucas")
CREDENTIALS_FILE        = os.environ.get("CREDENTIALS_FILE", "credentials.json")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")

SHEET_CLIENTES   = "CLIENTES"
SHEET_STOCK      = "STOCK"
SHEET_PRECIOS    = "CATALOGO"
SHEET_VENTAS     = "VENTAS"

TIPOS_CLIENTE_VALIDOS = ("Minorista", "Mayorista")
ESTADOS_PEDIDO = ("Reservado", "Entregado sin pago", "Pagado", "Cancelado", "Entregado", "COMODATO")

# Estados del ConversationHandler de /nuevo
(
    FILTRO_CLIENTE, BUSCAR_CLIENTE, ELEGIR_CLIENTE, ELEGIR_PRODUCTO, INGRESAR_CANTIDAD,
    CONFIRMAR_PRECIO, NC_RESPONSABLE, NC_TIPO, NC_TELEFONO, NC_DIRECCION, NC_PISO, NC_LOCALIDAD,
) = range(12)

# Estados del ConversationHandler de /estado
EST_ELEGIR_PEDIDO, EST_ELEGIR_ESTADO = range(2)

logging.basicConfig(level=logging.WARNING)

# ─────────────────────────────────────────────
#  ACCESO A GOOGLE SHEETS
# ─────────────────────────────────────────────
def _spreadsheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if GOOGLE_CREDENTIALS_JSON:
        info = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = Credentials.from_service_account_info(info, scopes=scopes)
    else:
        credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    return gspread.authorize(credentials).open_by_key(SPREADSHEET_ID)

def cargar_clientes():
    return _spreadsheet().worksheet(SHEET_CLIENTES).get_all_records()

def cargar_stock():
    records = _spreadsheet().worksheet(SHEET_STOCK).get_all_records()
    return {r["Producto"]: r for r in records if r.get("Producto")}

def cargar_precios():
    records = _spreadsheet().worksheet(SHEET_PRECIOS).get_all_records()
    precios = {}
    for r in records:
        producto = r.get("Producto")
        if not producto:
            continue
        # La planilla trae filas duplicadas por producto (plantillas para
        # futuros cambios de precio) sin precio cargado todavía — se ignoran
        # para no pisar la fila con el precio vigente.
        tiene_precio = str(r.get("Precio publico", "")).strip() or str(r.get("precio mayorista", "")).strip()
        if not tiene_precio:
            continue
        precios[producto] = r
    return precios

def guardar_fila_venta(fila: list):
    ws = _spreadsheet().worksheet(SHEET_VENTAS)
    ws.append_row(fila, value_input_option="USER_ENTERED")

def pedidos_abiertos():
    records = _spreadsheet().worksheet(SHEET_VENTAS).get_all_records()
    pedidos = {}
    for r in records:
        estado = r.get("Estado")
        if estado not in ("Reservado", "Entregado sin pago", "COMODATO"):
            continue
        np = r.get("Nro Pedido")
        if not np:
            continue
        pedidos.setdefault(np, {"cliente": r.get("Cliente", ""), "estado": estado})
    return pedidos

def actualizar_estado_pedido(nro_pedido: str, nuevo_estado: str) -> int:
    ws   = _spreadsheet().worksheet(SHEET_VENTAS)
    vals = ws.get_all_values()
    headers = vals[0]
    col_np     = headers.index("Nro Pedido")
    col_estado = headers.index("Estado") + 1  # gspread usa columnas 1-indexadas

    filas = [i for i, row in enumerate(vals[1:], start=2)
             if len(row) > col_np and row[col_np] == nro_pedido]
    for fila in filas:
        ws.update_cell(fila, col_estado, nuevo_estado)
    return len(filas)

def guardar_cliente_nuevo(nombre_local, nombre_responsable, tipo_cliente, telefono, direccion, piso_depto, localidad):
    # "ID Cliente" (col A) y "Whatsapp" (col F) se calculan solos con un
    # ARRAYFORMULA que ya cubre toda la columna — no hay que escribirles nada,
    # y escribirles un valor literal rompería esa fórmula. "Ubicacion" (col J)
    # ya tiene la fórmula copiada varias filas hacia abajo de antemano.
    ws   = _spreadsheet().worksheet(SHEET_CLIENTES)
    fila = len(ws.col_values(2)) + 1  # próxima fila libre según "Nombre Local"
    ws.update(range_name=f"B{fila}:E{fila}",
              values=[[nombre_local, nombre_responsable, tipo_cliente, telefono]],
              value_input_option="USER_ENTERED")
    ws.update(range_name=f"G{fila}:I{fila}",
              values=[[direccion, piso_depto, localidad]],
              value_input_option="USER_ENTERED")

# ─────────────────────────────────────────────
#  UTILIDADES
# ─────────────────────────────────────────────
def fmt_precio(valor) -> str:
    # valor puede ser un numero ya calculado (ej: cantidad * precio) o un
    # string crudo de la planilla en formato argentino (ej: "$8.000"). Si ya
    # es numerico hay que usarlo tal cual: tratarlo como string y sacarle el
    # "." como separador de miles multiplicaria el valor por 10 o mas.
    if isinstance(valor, (int, float)):
        n = valor
    else:
        try:
            n = float(str(valor).replace("$", "").strip().replace(".", "").replace(",", "."))
        except Exception:
            return str(valor)
    return f"${int(round(n)):,}".replace(",", ".")

def limpiar_precio(valor) -> float:
    try:
        return float(str(valor).replace("$", "").replace(".", "").replace(",", "."))
    except Exception:
        return 0.0

def link_whatsapp(telefono, mensaje: str) -> str:
    tel = str(telefono).strip().replace(" ", "").replace("-", "")
    if tel.startswith("0"):
        tel = tel[1:]
    if not tel.startswith("549"):
        tel = "549" + tel
    return f"https://wa.me/{tel}?text={quote(mensaje)}"

def nombre_mostrar(row: dict, tipo: str = None) -> str:
    # Minorista -> el responsable (la persona que compra).
    # Mayorista -> el nombre del negocio.
    if tipo is None:
        tipo = (row.get("Tipo de cliente") or "").strip()
    if tipo == "Minorista":
        return (row.get("Nombre Responsable") or row.get("Nombre Local") or "").strip()
    return (row.get("Nombre Local") or row.get("Nombre Responsable") or "").strip()

def normalizar_cliente(row: dict) -> dict:
    tipo = (row.get("Tipo de cliente") or "").strip()
    if tipo not in TIPOS_CLIENTE_VALIDOS:
        tipo = "Minorista"
    return {
        "ID Cliente": row.get("ID Cliente", ""),
        "Nombre": nombre_mostrar(row, tipo),
        # La columna "Cliente" de VENTAS tiene una validacion estricta que
        # solo acepta valores de CLIENTES!C (Nombre Responsable) — hay que
        # guardar siempre este nombre ahi, aunque el bot muestre otro en
        # Telegram/WhatsApp.
        "Nombre Responsable": (row.get("Nombre Responsable") or row.get("Nombre Local") or "").strip(),
        "Telefono": row.get("Telefono", ""),
        "Tipo de cliente": tipo,
    }

def precio_y_margen(catalogo_row: dict, tipo_cliente: str):
    if tipo_cliente == "Mayorista":
        precio = limpiar_precio(catalogo_row.get("precio mayorista", 0))
        margen = limpiar_precio(catalogo_row.get("Margen unitario Mayorista", 0))
    else:
        precio = limpiar_precio(catalogo_row.get("Precio publico", 0))
        margen = limpiar_precio(catalogo_row.get("Margen unitario Minorista", 0))
    return precio, margen

# ─────────────────────────────────────────────
#  COMANDOS DEL BOT
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🍯 *Bot de Ventas — {BRAND_NAME}*\n\n"
        "🛒 /nuevo      → Cargar un pedido\n"
        "📦 /stock      → Ver stock disponible\n"
        "⏳ /pendientes → Pedidos sin entregar\n"
        "🔄 /estado     → Cambiar el estado de un pedido\n"
        "👥 /clientes   → Lista de clientes\n"
        "❌ /cancelar   → Cancelar operación actual",
        parse_mode="Markdown",
    )

async def cmd_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Cargando stock…")
    try:
        stock = cargar_stock()

        texto = "📦 *Stock actual*\n\n"
        for nombre, d in sorted(stock.items()):
            disp  = int(d.get("Disponible") or 0)
            icono = "✅" if disp > 0 else "❌"
            texto += f"  {icono} 🍯 {nombre}: *{disp}*\n"

        await msg.edit_text(texto, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

async def cmd_pendientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Cargando pendientes…")
    try:
        ws       = _spreadsheet().worksheet(SHEET_VENTAS)
        records  = ws.get_all_records()
        reservas = [r for r in records if r.get("Estado") == "Reservado"]

        if not reservas:
            await msg.edit_text("✅ No hay pedidos pendientes.")
            return

        por_cliente: dict = {}
        for r in reservas:
            c = r.get("Cliente", "—")
            por_cliente.setdefault(c, []).append(r)

        texto = "⏳ *Pedidos Reservados*\n\n"
        for cliente, items in por_cliente.items():
            texto += f"👤 *{cliente}*\n"
            for it in items:
                texto += (
                    f"  • {it.get('Cantidad')}x {it.get('Producto')} "
                    f"— {fmt_precio(it.get('Total', 0))}\n"
                )
            texto += "\n"

        await msg.edit_text(texto, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
#  CAMBIAR ESTADO DE UN PEDIDO
# ─────────────────────────────────────────────

async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Cargando pedidos…")
    try:
        pedidos = pedidos_abiertos()
        if not pedidos:
            await msg.edit_text("✅ No hay pedidos Reservados ni Entregados sin pago.")
            return ConversationHandler.END

        context.user_data["pedidos_estado"] = pedidos
        teclado = [
            [InlineKeyboardButton(f"{np} — {info['cliente']} ({info['estado']})", callback_data=f"pedido|{np}")]
            for np, info in pedidos.items()
        ]
        await msg.edit_text(
            "🔄 *¿Qué pedido querés actualizar?*",
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        return EST_ELEGIR_PEDIDO
    except Exception as e:
        await msg.edit_text(f"❌ Error al cargar pedidos: {e}")
        return ConversationHandler.END

async def cb_elegir_pedido_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, nro_pedido = query.data.split("|", 1)
    context.user_data["pedido_actual"] = nro_pedido

    teclado = [[InlineKeyboardButton(e, callback_data=f"nuevoestado|{e}")] for e in ESTADOS_PEDIDO]
    await query.edit_message_text(
        f"🔄 Pedido *{nro_pedido}*\n¿Nuevo estado?",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )
    return EST_ELEGIR_ESTADO

async def cb_elegir_nuevo_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, nuevo_estado = query.data.split("|", 1)
    nro_pedido = context.user_data["pedido_actual"]

    await query.edit_message_text("⏳ Actualizando…")
    try:
        cantidad_filas = actualizar_estado_pedido(nro_pedido, nuevo_estado)
        await query.edit_message_text(
            f"✅ Pedido *{nro_pedido}* → *{nuevo_estado}* ({cantidad_filas} línea/s actualizadas)",
            parse_mode="Markdown",
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Error al actualizar: {e}")

    return ConversationHandler.END

async def cmd_clientes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Cargando…")
    try:
        clientes = cargar_clientes()
        texto = "👥 *Clientes*\n\n"
        for c in clientes:
            nombre = nombre_mostrar(c)
            tel       = c.get("Telefono", "")
            localidad = c.get("Localidad", "")
            tipo      = c.get("Tipo de cliente", "")
            if nombre:
                texto += f"• *{nombre}*"
                if tipo:
                    texto += f"  🏷 {tipo}"
                if tel:
                    texto += f"  📱 {tel}"
                if localidad:
                    texto += f"  📍 {localidad}"
                texto += "\n"
        await msg.edit_text(texto, parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: {e}")

# ─────────────────────────────────────────────
#  NUEVO PEDIDO - Paso 1: Elegir cliente
# ─────────────────────────────────────────────

async def cmd_nuevo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["items"] = []

    msg = await update.message.reply_text("⏳ Cargando clientes…")
    try:
        context.user_data["clientes"] = cargar_clientes()

        teclado = [
            [InlineKeyboardButton("📋 Ver todos", callback_data="filtro|todos")],
            [InlineKeyboardButton("🔍 Buscar por nombre", callback_data="filtro|buscar")],
            [InlineKeyboardButton("✏️ Cliente nuevo", callback_data="cli|_manual_")],
        ]
        await msg.edit_text(
            "👤 *¿Para quién es el pedido?*",
            reply_markup=InlineKeyboardMarkup(teclado),
            parse_mode="Markdown",
        )
        return FILTRO_CLIENTE
    except Exception as e:
        await msg.edit_text(f"❌ Error al cargar clientes: {e}")
        return ConversationHandler.END

async def _mostrar_lista_clientes(origen, context: ContextTypes.DEFAULT_TYPE, clientes_filtrados: list):
    teclado = []
    for c in clientes_filtrados:
        nombre = nombre_mostrar(c)
        if nombre:
            teclado.append([InlineKeyboardButton(nombre, callback_data=f"cli|{c['ID Cliente']}")])
    teclado.append([InlineKeyboardButton("✏️ Cliente nuevo", callback_data="cli|_manual_")])

    texto = (
        "👤 *¿Para quién es el pedido?*" if teclado[:-1]
        else "👤 No encontré clientes con ese filtro.\n¿Cargamos uno nuevo?"
    )
    markup = InlineKeyboardMarkup(teclado)

    if hasattr(origen, "edit_message_text"):
        await origen.edit_message_text(texto, reply_markup=markup, parse_mode="Markdown")
    else:
        await origen.message.reply_text(texto, reply_markup=markup, parse_mode="Markdown")
    return ELEGIR_CLIENTE

async def cb_filtro_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, valor = query.data.split("|", 1)

    if valor == "buscar":
        await query.edit_message_text("🔍 Escribí las primeras letras del nombre del cliente:")
        return BUSCAR_CLIENTE

    clientes = context.user_data.get("clientes", [])
    return await _mostrar_lista_clientes(query, context, clientes)

async def msg_buscar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip().lower()
    clientes = context.user_data.get("clientes", [])
    filtrados = [
        c for c in clientes
        if texto in f"{c.get('Nombre Local', '')} {c.get('Nombre Responsable', '')}".lower()
    ]
    return await _mostrar_lista_clientes(update, context, filtrados)

async def cb_elegir_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, valor = query.data.split("|", 1)

    if valor == "_manual_":
        await query.edit_message_text("✏️ Escribí el nombre del cliente:")
        return ELEGIR_CLIENTE

    clientes = context.user_data.get("clientes", [])
    cliente  = next((c for c in clientes if str(c["ID Cliente"]) == valor), None)
    if not cliente:
        await query.edit_message_text("❌ Cliente no encontrado.")
        return ConversationHandler.END

    context.user_data["cliente"] = normalizar_cliente(cliente)
    return await _mostrar_menu_productos(query, context)

async def msg_nombre_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nombre = update.message.text.strip()
    if not nombre:
        await update.message.reply_text("⚠️ Escribí un nombre válido:")
        return ELEGIR_CLIENTE

    context.user_data["nuevo_cliente"] = {"Nombre Local": nombre}
    await update.message.reply_text("🙋 ¿Nombre del responsable / contacto?")
    return NC_RESPONSABLE

async def msg_nc_responsable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    responsable = update.message.text.strip()
    context.user_data["nuevo_cliente"]["Nombre Responsable"] = responsable

    teclado = [
        [InlineKeyboardButton("Minorista", callback_data="nctipo|Minorista")],
        [InlineKeyboardButton("Mayorista", callback_data="nctipo|Mayorista")],
    ]
    await update.message.reply_text(
        "🏷 ¿Tipo de cliente?",
        reply_markup=InlineKeyboardMarkup(teclado),
    )
    return NC_TIPO

async def cb_nc_tipo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, tipo = query.data.split("|", 1)
    context.user_data["nuevo_cliente"]["Tipo de cliente"] = tipo
    await query.edit_message_text(f"🏷 Tipo de cliente: *{tipo}*", parse_mode="Markdown")
    await query.message.reply_text("📱 ¿Teléfono? (solo números, sin 0 ni 15)")
    return NC_TELEFONO

async def msg_nc_telefono(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nuevo_cliente"]["Telefono"] = update.message.text.strip()
    await update.message.reply_text("📍 ¿Dirección?")
    return NC_DIRECCION

async def msg_nc_direccion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nuevo_cliente"]["Direccion"] = update.message.text.strip()
    await update.message.reply_text("🚪 ¿Piso/Depto? (dejá un espacio o guión si no aplica)")
    return NC_PISO

async def msg_nc_piso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nuevo_cliente"]["Piso/Depto"] = update.message.text.strip()
    await update.message.reply_text("🌆 ¿Localidad?")
    return NC_LOCALIDAD

async def msg_nc_localidad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nc = context.user_data["nuevo_cliente"]
    nc["Localidad"] = update.message.text.strip()

    msg = await update.message.reply_text("⏳ Guardando cliente nuevo…")
    try:
        guardar_cliente_nuevo(
            nombre_local=nc["Nombre Local"],
            nombre_responsable=nc.get("Nombre Responsable", ""),
            tipo_cliente=nc.get("Tipo de cliente", "Minorista"),
            telefono=nc.get("Telefono", ""),
            direccion=nc.get("Direccion", ""),
            piso_depto=nc.get("Piso/Depto", ""),
            localidad=nc["Localidad"],
        )
    except Exception as e:
        await msg.edit_text(f"❌ Error al guardar el cliente: {e}")
        return ConversationHandler.END

    context.user_data["cliente"] = normalizar_cliente({
        "Nombre Local": nc["Nombre Local"],
        "Nombre Responsable": nc.get("Nombre Responsable", ""),
        "Tipo de cliente": nc.get("Tipo de cliente", "Minorista"),
        "Telefono": nc.get("Telefono", ""),
    })
    await msg.edit_text(f"✅ Cliente *{nc['Nombre Local']}* guardado.", parse_mode="Markdown")
    return await _mostrar_menu_productos(update, context)

# ─────────────────────────────────────────────
#  PASO 2: Menú de productos
# ─────────────────────────────────────────────

async def _mostrar_menu_productos(origen, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock   = cargar_stock()
        precios = cargar_precios()
        context.user_data["stock"]   = stock
        context.user_data["precios"] = precios

        tipo_cliente = context.user_data["cliente"]["Tipo de cliente"]
        disponibles  = [(k, v) for k, v in stock.items()
                        if int(v.get("Disponible") or 0) > 0]

        items   = context.user_data.get("items", [])
        cliente = context.user_data["cliente"]["Nombre"]

        texto = f"🛒 *Pedido para {cliente}* ({tipo_cliente})\n"
        if items:
            texto += "\n*Cargado hasta ahora:*\n"
            subtotal = 0
            for it in items:
                s = it["cantidad"] * it["precio"]
                subtotal += s
                texto += f"  • {it['cantidad']}x {it['producto']}: {fmt_precio(s)}\n"
            texto += f"  ─────────────\n  *Subtotal: {fmt_precio(subtotal)}*\n"

        texto += "\n📦 *Elegí un producto:*"

        teclado = []
        for nombre, d in sorted(disponibles):
            disp   = int(d.get("Disponible") or 0)
            precio, _ = precio_y_margen(precios.get(nombre, {}), tipo_cliente)
            teclado.append([InlineKeyboardButton(
                f"🍯 {nombre}  (x{disp}) {fmt_precio(precio)}",
                callback_data=f"prod|{nombre}",
            )])

        if items:
            teclado.append([InlineKeyboardButton("✅ Confirmar pedido", callback_data="accion|confirmar")])
        teclado.append([InlineKeyboardButton("❌ Cancelar", callback_data="accion|cancelar")])

        markup = InlineKeyboardMarkup(teclado)

        if hasattr(origen, "edit_message_text"):
            await origen.edit_message_text(texto, reply_markup=markup, parse_mode="Markdown")
        else:
            await origen.message.reply_text(texto, reply_markup=markup, parse_mode="Markdown")

        return ELEGIR_PRODUCTO

    except Exception as e:
        if hasattr(origen, "edit_message_text"):
            await origen.edit_message_text(f"❌ Error: {e}")
        else:
            await origen.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

async def cb_elegir_producto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tipo, valor = query.data.split("|", 1)

    if tipo == "accion":
        if valor == "confirmar":
            return await _confirmar_y_guardar(query, context)
        else:
            await query.edit_message_text("❌ Pedido cancelado.")
            return ConversationHandler.END

    producto     = valor
    stock        = context.user_data.get("stock", {})
    precios      = context.user_data.get("precios", {})
    tipo_cliente = context.user_data["cliente"]["Tipo de cliente"]
    disp         = int(stock.get(producto, {}).get("Disponible") or 0)
    precio, margen = precio_y_margen(precios.get(producto, {}), tipo_cliente)

    context.user_data["producto_seleccionado"] = producto
    context.user_data["precio_seleccionado"]   = precio
    context.user_data["margen_seleccionado"]   = margen

    await query.edit_message_text(
        f"📦 *{producto}*\n"
        f"💰 Precio ({tipo_cliente}): {fmt_precio(precio)}\n"
        f"📊 Disponible: *{disp}*\n\n"
        f"¿Cuántas unidades querés agregar?",
        parse_mode="Markdown",
    )
    return INGRESAR_CANTIDAD

async def msg_ingresar_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()

    if not texto.isdigit() or int(texto) <= 0:
        await update.message.reply_text("⚠️ Ingresá un número entero mayor a 0:")
        return INGRESAR_CANTIDAD

    cantidad = int(texto)
    producto = context.user_data["producto_seleccionado"]
    stock    = context.user_data["stock"]
    disp     = int(stock.get(producto, {}).get("Disponible") or 0)

    if cantidad > disp:
        await update.message.reply_text(
            f"⚠️ Solo hay *{disp}* disponibles. Ingresá un número ≤ {disp}:",
            parse_mode="Markdown",
        )
        return INGRESAR_CANTIDAD

    context.user_data["cantidad_pendiente"] = cantidad
    precio = context.user_data["precio_seleccionado"]

    teclado = [
        [InlineKeyboardButton(f"💰 Precio normal ({fmt_precio(precio)})", callback_data="preciolinea|normal")],
        [InlineKeyboardButton("🎁 Muestra gratis ($0)", callback_data="preciolinea|gratis")],
    ]
    await update.message.reply_text(
        f"¿*{cantidad}x {producto}* a precio normal o es muestra gratis?",
        reply_markup=InlineKeyboardMarkup(teclado),
        parse_mode="Markdown",
    )
    return CONFIRMAR_PRECIO

async def cb_confirmar_precio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, valor = query.data.split("|", 1)

    cantidad     = context.user_data["cantidad_pendiente"]
    producto     = context.user_data["producto_seleccionado"]
    tipo_cliente = context.user_data["cliente"]["Tipo de cliente"]

    if valor == "gratis":
        precio, margen = 0, 0
    else:
        precio = context.user_data["precio_seleccionado"]
        margen = context.user_data["margen_seleccionado"]

    items = context.user_data["items"]
    existente = next((i for i in items if i["producto"] == producto and i["precio"] == precio), None)
    if existente:
        existente["cantidad"] += cantidad
    else:
        items.append({
            "producto": producto, "cantidad": cantidad,
            "precio": precio, "margen_unit": margen,
            "tipo_venta": tipo_cliente,
        })

    etiqueta = "🎁 muestra gratis" if valor == "gratis" else fmt_precio(precio)
    await query.edit_message_text(f"✅ Agregado: *{cantidad}x {producto}* ({etiqueta})", parse_mode="Markdown")
    return await _mostrar_menu_productos(query, context)

# ─────────────────────────────────────────────
#  PASO 3: Confirmar y guardar en Google Sheets
# ─────────────────────────────────────────────

async def _confirmar_y_guardar(query, context: ContextTypes.DEFAULT_TYPE):
    items   = context.user_data.get("items", [])
    cliente = context.user_data.get("cliente", {})

    if not items:
        await query.edit_message_text("⚠️ No hay productos en el pedido.")
        return ConversationHandler.END

    await query.edit_message_text("⏳ Guardando en Google Sheets…")

    try:
        fecha             = datetime.now().strftime("%d/%m/%Y")
        fecha_compacta    = datetime.now().strftime("%Y%m%d")
        nombre_cliente    = cliente.get("Nombre", "")
        nombre_responsable = cliente.get("Nombre Responsable") or nombre_cliente
        nro_pedido        = f"{fecha_compacta}_{nombre_responsable}"
        stock             = context.user_data.get("stock", {})
        total_gral     = sum(i["cantidad"] * i["precio"] for i in items)

        for item in items:
            prod       = item["producto"]
            cant       = item["cantidad"]
            precio     = item["precio"]
            total      = cant * precio
            tipo_venta = item["tipo_venta"]
            disp       = int(stock.get(prod, {}).get("Disponible") or 0)
            chequeo    = "OK" if disp >= cant else "SIN STOCK"
            m_unit     = item["margen_unit"]
            m_total    = m_unit * cant

            fila = [
                fecha,                          # A: Fecha
                nombre_cliente,                 # B: Nombre local (negocio si es mayorista)
                nombre_responsable,             # C: Cliente (validado contra CLIENTES!C)
                prod,                           # D: Producto
                cant,                           # E: Cantidad
                tipo_venta,                     # F: Tipo de venta
                precio,                         # G: Precio unitario
                total,                          # H: Total
                "Reservado",                    # I: Estado
                disp,                           # J: Stock disponible del producto elegido
                chequeo,                        # K: Chequeo
                m_total,                        # L: Margen total
                m_unit,                         # M: Margen unitario
                "",                             # N: Whatsapp
                nro_pedido,                     # O: Nro Pedido
                "",                             # P: Cubre con pendiente?
                "",                             # Q: Cuanto queda post reservas
            ]
            guardar_fila_venta(fila)

        lineas = "\n".join(
            f"  • {i['cantidad']}x {i['producto']}: {fmt_precio(i['cantidad'] * i['precio'])}"
            for i in items
        )
        lineas_wa = "\n".join(
            f"- {i['producto']} x{i['cantidad']}"
            for i in items
        )
        mensaje_wa = (
            f"Hola {nombre_cliente}! 🍯\n"
            f"Te confirmo tu pedido:\n"
            f"{lineas_wa}\n"
            f"*Total:* {fmt_precio(total_gral)}\n"
            f"Gracias por tu compra 🍯✨"
        )

        telefono = cliente.get("Telefono", "") or ""
        url_wa   = link_whatsapp(telefono, mensaje_wa) if telefono else None

        resumen = (
            f"✅ *Pedido #{nro_pedido} guardado!*\n\n"
            f"👤 {nombre_cliente}\n"
            f"{lineas}\n"
            f"─────────────\n"
            f"💰 *Total: {fmt_precio(total_gral)}*"
        )

        teclado = []
        if url_wa:
            teclado.append([InlineKeyboardButton("📱 Enviar por WhatsApp", url=url_wa)])

        await query.edit_message_text(
            resumen,
            reply_markup=InlineKeyboardMarkup(teclado) if teclado else None,
            parse_mode="Markdown",
        )

    except Exception as e:
        await query.edit_message_text(f"❌ Error al guardar: {e}")

    return ConversationHandler.END

async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

import asyncio

def main():
    if not TELEGRAM_TOKEN:
        raise SystemExit("Falta la variable de entorno TELEGRAM_TOKEN.")

    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("nuevo", cmd_nuevo)],
        states={
            FILTRO_CLIENTE: [
                CallbackQueryHandler(cb_filtro_cliente, pattern=r"^filtro\|"),
                CallbackQueryHandler(cb_elegir_cliente, pattern=r"^cli\|"),
            ],
            BUSCAR_CLIENTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_buscar_cliente),
            ],
            ELEGIR_CLIENTE: [
                CallbackQueryHandler(cb_elegir_cliente, pattern=r"^cli\|"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_nombre_manual),
            ],
            NC_RESPONSABLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_nc_responsable),
            ],
            NC_TIPO: [
                CallbackQueryHandler(cb_nc_tipo, pattern=r"^nctipo\|"),
            ],
            NC_TELEFONO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_nc_telefono),
            ],
            NC_DIRECCION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_nc_direccion),
            ],
            NC_PISO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_nc_piso),
            ],
            NC_LOCALIDAD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_nc_localidad),
            ],
            ELEGIR_PRODUCTO: [
                CallbackQueryHandler(cb_elegir_producto, pattern=r"^prod\|"),
                CallbackQueryHandler(cb_elegir_producto, pattern=r"^accion\|"),
            ],
            INGRESAR_CANTIDAD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, msg_ingresar_cantidad),
            ],
            CONFIRMAR_PRECIO: [
                CallbackQueryHandler(cb_confirmar_precio, pattern=r"^preciolinea\|"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    conv_estado = ConversationHandler(
        entry_points=[CommandHandler("estado", cmd_estado)],
        states={
            EST_ELEGIR_PEDIDO: [
                CallbackQueryHandler(cb_elegir_pedido_estado, pattern=r"^pedido\|"),
            ],
            EST_ELEGIR_ESTADO: [
                CallbackQueryHandler(cb_elegir_nuevo_estado, pattern=r"^nuevoestado\|"),
            ],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("stock",      cmd_stock))
    app.add_handler(CommandHandler("pendientes", cmd_pendientes))
    app.add_handler(CommandHandler("clientes",   cmd_clientes))
    app.add_handler(conv)
    app.add_handler(conv_estado)

    print("Bot iniciado. Ctrl+C para detener.")
    app.run_polling()

if __name__ == "__main__":
    main()
