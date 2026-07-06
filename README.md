# Bot de Ventas — Miel (Lucas)

Adaptado del bot de Helados & Frutos Secos, pero ajustado a la estructura real
de la planilla de Lucas (que ya existía y difiere bastante de la de helados).

## Planilla (Google Sheets)
ID: `19psQEs7UHpEa1SJFfeyy1UnxkVtE1Ay6yj0pd9Ug7Lk`

### Hoja CLIENTES
`ID Cliente | Nombre Local | Nombre Responsable | Tipo de cliente | Telefono | Whatsapp | Direccion | Localidad | Ubicacion`

- El bot muestra y guarda `Nombre Local` como nombre del cliente (si está
  vacío, usa `Nombre Responsable`).
- `Tipo de cliente` debe ser `Minorista` o `Mayorista` — define qué precio y
  margen se usan de la hoja CATALOGO. Si está vacío o tiene otro valor, se
  asume `Minorista`.

### Hoja STOCK
`Producto | Ingresado | Vendido | Reservado | Disponible`

No tiene categoría de producto (a diferencia del bot de helados), así que
`/stock` muestra todo en una sola lista ordenada alfabéticamente.

### Hoja CATALOGO
`Producto | Precio publico | precio mayorista | Margen unitario Minorista | Margen unitario Mayorista | Fecha desde | Fecha hasta precio`

El precio y el margen que se le cobran/calculan a cada cliente dependen de su
`Tipo de cliente` en la hoja CLIENTES.

### Hoja VENTAS
A: Fecha | B: Cliente | C: Producto | D: Cantidad | E: Tipo de venta |
F: Precio unitario | G: Total | H: Estado | I: Stock disponible del producto
elegido | J: Chequeo | K: Margen total | L: Margen unitario | M: Whatsapp |
N: Nro Pedido | O: Cubre con pendiente? | P: Cuanto queda post reservas

El bot arma `Nro Pedido` como `AAAAMMDD_NombreCliente` (ej: `20260706_Pirulito`),
igual al formato que ya venía usando Lucas en la planilla.

## Comandos del bot
- `/start` — Menú principal
- `/nuevo` — Cargar un pedido (flujo guiado, precio según tipo de cliente)
- `/stock` — Ver stock disponible
- `/pendientes` — Pedidos con estado Reservado
- `/clientes` — Lista de clientes
- `/cancelar` — Cancelar operación actual

## Configuración

El bot lee todo de variables de entorno — no hay secretos escritos en el
código. Variables usadas:

| Variable | Obligatoria | Descripción |
|---|---|---|
| `TELEGRAM_TOKEN` | Sí | Token que da @BotFather |
| `GOOGLE_CREDENTIALS_JSON` | Sí (en la nube) | Contenido completo de `credentials.json` (la service account), pegado como un solo string JSON |
| `SPREADSHEET_ID` | No | Por defecto ya apunta a la planilla de Lucas |
| `BRAND_NAME` | No | Por defecto "Miel Lucas" |
| `CREDENTIALS_FILE` | No | Solo se usa si `GOOGLE_CREDENTIALS_JSON` está vacía (para correrlo local con el archivo `credentials.json`) |

## Correrlo en tu compu (para probar)

```bash
pip install -r requirements.txt
```

Crear un archivo `.env` o exportar la variable antes de correr:

```bash
export TELEGRAM_TOKEN="el_token_de_botfather"
python bot.py
```

(En Windows PowerShell: `$env:TELEGRAM_TOKEN="el_token"; python bot.py`)

Como `credentials.json` ya está en esta carpeta, no hace falta configurar
`GOOGLE_CREDENTIALS_JSON` para probar local.

## Correrlo 24/7 en Railway (sin depender de tu compu)

1. **Crear el bot en @BotFather** (si todavía no lo hiciste):
   - En Telegram, buscar `@BotFather` y enviarle `/newbot`.
   - Elegir un nombre visible (ej: "Miel Lucas Bot") y un username único
     terminado en `bot` (ej: `mielucas_bot`).
   - Copiar el token que te devuelve.

2. **Subir este código a un repositorio de GitHub** (privado, recomendado) —
   sin el archivo `credentials.json` (el `.gitignore` ya lo excluye).

3. **Crear cuenta en [railway.app](https://railway.app)** con
   `soletissone@gmail.com` (botón "Login with Google").

4. **New Project → Deploy from GitHub repo** y elegir el repositorio del bot.

5. En la pestaña **Variables** del servicio, cargar:
   - `TELEGRAM_TOKEN` → el token de BotFather
   - `GOOGLE_CREDENTIALS_JSON` → abrir el archivo `credentials.json` local
     con el Bloc de notas, copiar TODO el contenido (desde `{` hasta `}`) y
     pegarlo como valor de esta variable
   - (opcional) `BRAND_NAME` si "Miel Lucas" no es el nombre correcto

6. Railway detecta el `Procfile` (`worker: python bot.py`) y lo deja
   corriendo solo, 24/7, sin depender de que tu compu esté prendida ni de la
   red de UTEL.
