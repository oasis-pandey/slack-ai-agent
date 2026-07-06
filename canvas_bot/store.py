import os
import sqlite3
import dataclasses
from cryptography.fernet import Fernet

try:
    import psycopg
except ImportError:
    psycopg = None

@dataclasses.dataclass
class CanvasCreds:
    canvas_base_url: str
    canvas_api_url: str
    canvas_token: str

def _get_fernet():
    key = os.environ.get("CREDS_ENC_KEY")
    if not key:
        raise ValueError("CREDS_ENC_KEY environment variable is not set")
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)

def _get_db_connection():
    db_url = os.environ.get("DATABASE_URL")
    if db_url and psycopg:
        conn = psycopg.connect(db_url)
        return conn, "%s"
    
    # Fallback to SQLite
    db_path = os.environ.get("SQLITE_DB_PATH", "./data/creds.db")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    return conn, "?"

def _init_db():
    conn, ph = _get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_creds (
                slack_user_id VARCHAR(255) PRIMARY KEY,
                canvas_base_url TEXT NOT NULL,
                canvas_api_url TEXT NOT NULL,
                encrypted_token TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

def get_creds(slack_user_id: str) -> CanvasCreds | None:
    conn, ph = _get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT canvas_base_url, canvas_api_url, encrypted_token FROM user_creds WHERE slack_user_id = {ph}", (slack_user_id,))
        row = cursor.fetchone()
        if not row:
            return None
        
        base_url, api_url, encrypted_token = row
        fernet = _get_fernet()
        token = fernet.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")
        
        return CanvasCreds(
            canvas_base_url=base_url,
            canvas_api_url=api_url,
            canvas_token=token
        )
    finally:
        conn.close()

def set_creds(slack_user_id: str, creds: CanvasCreds) -> None:
    fernet = _get_fernet()
    encrypted_token = fernet.encrypt(creds.canvas_token.encode("utf-8")).decode("utf-8")
    
    conn, ph = _get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            INSERT INTO user_creds (slack_user_id, canvas_base_url, canvas_api_url, encrypted_token)
            VALUES ({ph}, {ph}, {ph}, {ph})
            ON CONFLICT(slack_user_id) DO UPDATE SET
                canvas_base_url=EXCLUDED.canvas_base_url,
                canvas_api_url=EXCLUDED.canvas_api_url,
                encrypted_token=EXCLUDED.encrypted_token
            """,
            (slack_user_id, creds.canvas_base_url, creds.canvas_api_url, encrypted_token)
        )
        conn.commit()
    finally:
        conn.close()

def delete_creds(slack_user_id: str) -> None:
    conn, ph = _get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"DELETE FROM user_creds WHERE slack_user_id = {ph}", (slack_user_id,))
        conn.commit()
    finally:
        conn.close()

# Initialize on import (safe for SQLite locally; Railway Postgres works too)
_init_db()
