import urllib.request

TOK = "8376616657:AAE1CIMgn_8ly9h_vGiv2yVMMF4dx4VFvUY"
URL = "https://soledadtissone.pythonanywhere.com"

proxy = urllib.request.ProxyHandler({
    "https": "http://proxy.server:3128",
    "http": "http://proxy.server:3128",
})
opener = urllib.request.build_opener(proxy)
url = f"https://api.telegram.org/bot{TOK}/setWebhook?url={URL}/{TOK}"
print(opener.open(url).read().decode())
