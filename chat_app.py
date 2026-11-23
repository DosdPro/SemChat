import webview

# Адрес твоего сайта. Если сервер запущен на другом порту — поменяй 5000.
webview.create_window("SemChat", "http://127.0.0.1:5000",
                      width=1100, height=750)

webview.start()
