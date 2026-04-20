# إضافة في نهاية الملف قبل main()
from http.server import HTTPServer, BaseHTTPRequestHandler

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running')

def run_dummy_server():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# في main()
async def main():
    # ... تهيئة تيليجرام ...
    
    # تشغيل السيرفر الوهمي في Thread منفصل
    import threading
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    # تشغيل البوت
    await trading_loop()
