import os
import sqlite3
import pytest
from cryptography.fernet import Fernet
from canvas_bot.store import CanvasCreds, get_creds, set_creds, delete_creds, _get_db_connection

@pytest.fixture
def temp_creds_env(tmp_path):
    # Set up environment variables for testing
    db_path = tmp_path / "test_creds.db"
    enc_key = Fernet.generate_key().decode("utf-8")
    
    os.environ["SQLITE_DB_PATH"] = str(db_path)
    os.environ["CREDS_ENC_KEY"] = enc_key
    
    # Ensure DATABASE_URL is not set so we fallback to sqlite for this test
    if "DATABASE_URL" in os.environ:
        del os.environ["DATABASE_URL"]
        
    yield
    
    # Cleanup
    if "SQLITE_DB_PATH" in os.environ:
        del os.environ["SQLITE_DB_PATH"]
    if "CREDS_ENC_KEY" in os.environ:
        del os.environ["CREDS_ENC_KEY"]

def test_store_round_trip(temp_creds_env):
    # Ensure the DB is initialized (it happens on import, but we changed env vars)
    from canvas_bot.store import _init_db
    _init_db()

    user_id = "U12345"
    creds = CanvasCreds(
        canvas_base_url="https://canvas.test",
        canvas_api_url="https://canvas.test/api/v1",
        canvas_token="super_secret_token_abc123"
    )
    
    # 1. Set creds
    set_creds(user_id, creds)
    
    # 2. Get creds
    loaded = get_creds(user_id)
    assert loaded is not None
    assert loaded.canvas_base_url == "https://canvas.test"
    assert loaded.canvas_api_url == "https://canvas.test/api/v1"
    assert loaded.canvas_token == "super_secret_token_abc123"
    
    # 3. Update creds
    creds.canvas_token = "new_token_456"
    set_creds(user_id, creds)
    loaded_updated = get_creds(user_id)
    assert loaded_updated.canvas_token == "new_token_456"
    
    # 4. Delete creds
    delete_creds(user_id)
    assert get_creds(user_id) is None

def test_token_is_encrypted_at_rest(temp_creds_env):
    from canvas_bot.store import _init_db
    _init_db()
    
    user_id = "U9999"
    creds = CanvasCreds(
        canvas_base_url="https://canvas.test",
        canvas_api_url="https://canvas.test/api/v1",
        canvas_token="my_plaintext_token"
    )
    set_creds(user_id, creds)
    
    # Manually check the database
    conn, _ = _get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT encrypted_token FROM user_creds WHERE slack_user_id = ?", (user_id,))
        row = cursor.fetchone()
        assert row is not None
        encrypted_token = row[0]
        
        # Ensure it's not the plaintext token
        assert encrypted_token != "my_plaintext_token"
        assert "my_plaintext_token" not in encrypted_token
    finally:
        conn.close()
