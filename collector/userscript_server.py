import os
import json
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

class TranscriptQueue:
    def __init__(self):
        self.queue = []
        self._load_queue()
        
    def _load_queue(self):
        metadata_file = DATA_DIR / "video_metadata.json"
        if not metadata_file.exists():
            logger.error(f"메타데이터 파일이 없습니다: {metadata_file}")
            return
            
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata_list = json.load(f)
            
        LIMIT_DATE = "20210514"
            
        for item in metadata_list:
            video_id = item["video_id"]
            upload_date = item.get("upload_date", "")
            
            # 5년 이전 영상 제외
            if upload_date and upload_date < LIMIT_DATE:
                continue
                
            # 이미 다운로드된 자막 제외
            output_file = TRANSCRIPT_DIR / f"{video_id}.json"
            if output_file.exists():
                continue
                
            self.queue.append(item)
            
        logger.info(f"대기 중인 자막 추출 작업 수: {len(self.queue)}")

    def get_next(self):
        if not self.queue:
            return None
        return self.queue[0]

    def mark_done(self, video_id):
        self.queue = [item for item in self.queue if item["video_id"] != video_id]
        logger.info(f"작업 완료: {video_id}. 남은 작업 수: {len(self.queue)}")

job_queue = TranscriptQueue()

class RequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/api/next":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            next_job = job_queue.get_next()
            if next_job:
                response = {"status": "ok", "video": next_job}
            else:
                response = {"status": "done"}
                
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed_path = urlparse(self.path)
        if parsed_path.path == "/api/save":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                data = json.loads(post_data.decode("utf-8"))
                video_id = data.get("video_id")
                transcript_text = data.get("transcript")
                upload_date = data.get("upload_date", "")
                title = data.get("title", "")
                
                if video_id and transcript_text:
                    output_file = TRANSCRIPT_DIR / f"{video_id}.json"
                    
                    result = {
                        "video_id": video_id,
                        "title": title,
                        "upload_date": upload_date,
                        "transcript": transcript_text
                    }
                    
                    with open(output_file, "w", encoding="utf-8") as f:
                        json.dump(result, f, ensure_ascii=False, indent=2)
                        
                    job_queue.mark_done(video_id)
                    
                    self.send_response(200)
                    self._send_cors_headers()
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "success"}).encode("utf-8"))
                else:
                    self.send_response(400)
                    self.end_headers()
            except Exception as e:
                logger.error(f"저장 중 오류 발생: {e}")
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # 콘솔이 너무 지저분해지지 않도록 기본 HTTP 로그는 무시
        pass

def run(server_class=HTTPServer, handler_class=RequestHandler, port=5000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logger.info("=" * 50)
    logger.info(f"✅ 유저 스크립트 통신용 로컬 서버 시작됨!")
    logger.info(f"🔗 접속 주소: http://localhost:{port}")
    logger.info(f"이제 브라우저에서 유튜브에 접속하면 자동으로 수집이 시작됩니다.")
    logger.info("=" * 50)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    logger.info("로컬 서버 종료")

if __name__ == "__main__":
    run()
