import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_videos (
            video_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            channel TEXT,
            source_url TEXT,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS upload_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            part_path TEXT NOT NULL,
            part_no INTEGER NOT NULL,
            total_parts INTEGER NOT NULL,
            title TEXT NOT NULL,
            channel TEXT,
            hashtags TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            account TEXT,
            error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            uploaded_at DATETIME
        );
        CREATE INDEX IF NOT EXISTS idx_upload_status ON upload_queue(status, id);
        """)
        self.conn.execute("UPDATE upload_queue SET status='pending' WHERE status='uploading'")
        self.conn.commit()

    def is_processed(self, video_id):
        return self.conn.execute(
            "SELECT 1 FROM processed_videos WHERE video_id=?", (video_id,)
        ).fetchone() is not None

    def mark_processed(self, video_id, title, channel, source_url):
        self.conn.execute(
            "INSERT OR IGNORE INTO processed_videos(video_id,title,channel,source_url) VALUES(?,?,?,?)",
            (video_id, title, channel, source_url),
        )
        self.conn.commit()

    def enqueue(self, video_id, part_path, part_no, total_parts, title, channel, hashtags):
        self.conn.execute(
            """INSERT INTO upload_queue
            (video_id,part_path,part_no,total_parts,title,channel,hashtags)
            VALUES(?,?,?,?,?,?,?)""",
            (video_id, str(part_path), part_no, total_parts, title, channel, hashtags),
        )
        self.conn.commit()

    def next_pending(self):
        return self.conn.execute(
            "SELECT * FROM upload_queue WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()

    def mark_uploading(self, queue_id, account):
        self.conn.execute(
            "UPDATE upload_queue SET status='uploading',account=?,error=NULL WHERE id=?",
            (account, queue_id),
        )
        self.conn.commit()

    def mark_uploaded(self, queue_id):
        self.conn.execute(
            "UPDATE upload_queue SET status='uploaded',uploaded_at=CURRENT_TIMESTAMP,error=NULL WHERE id=?",
            (queue_id,),
        )
        self.conn.commit()

    def retry(self, queue_id, error):
        self.conn.execute(
            "UPDATE upload_queue SET status='pending',error=? WHERE id=?",
            (str(error)[-1500:], queue_id),
        )
        self.conn.commit()
