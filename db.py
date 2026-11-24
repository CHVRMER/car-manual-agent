"""RDS(PostgreSQL + pgvector) 연결과 표 만들기."""
import os
from urllib.parse import quote

import psycopg
from dotenv import load_dotenv
from langchain_aws import BedrockEmbeddings
from langchain_postgres import PGVector

load_dotenv()

COLLECTION = "car_manuals"  # 모든 설명서 조각을 한 컬렉션에 두고 manual_id로 거름


def get_store(collection: str = COLLECTION) -> PGVector:
    embeddings = BedrockEmbeddings(model_id=os.environ["EMBED_MODEL_ID"],
                                   region_name=os.environ["EMBED_REGION"])
    url = (f"postgresql+psycopg://{os.environ['PG_USER']}:{quote(os.environ['PG_PASSWORD'])}"
           f"@{os.environ['PG_HOST']}:{os.environ['PG_PORT']}/{os.environ['PG_DB']}")
    return PGVector(embeddings=embeddings, collection_name=collection,
                    connection=url, use_jsonb=True)


def connect():
    return psycopg.connect(host=os.environ["PG_HOST"], port=os.environ["PG_PORT"],
                           dbname=os.environ["PG_DB"], user=os.environ["PG_USER"],
                           password=os.environ["PG_PASSWORD"], connect_timeout=10)


TABLES = """
CREATE TABLE IF NOT EXISTS manuals (
    manual_id TEXT PRIMARY KEY,          -- 파일 해시
    name TEXT NOT NULL,                  -- 설명서 이름 (예: CN8_2027). 정답 CSV의 manual_id와 연결
    car_model TEXT, powertrain TEXT, file_name TEXT,
    pages INT, chunks INT,
    severe_selected TEXT DEFAULT '',     -- 사용자가 해당한다고 체크한 가혹 조건 번호 (예: '1,4')
    schedule_confirmed BOOLEAN DEFAULT FALSE,
    uploaded_at TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS maintenance_schedule (
    id SERIAL PRIMARY KEY,
    manual_id TEXT NOT NULL, page INT, section TEXT, item TEXT, applies_to TEXT,
    action TEXT, schedule_type TEXT,
    interval_km INT, interval_months INT, first_km INT, first_months INT,
    severe_conditions TEXT, note TEXT, footnote TEXT, raw_text TEXT
);
CREATE TABLE IF NOT EXISTS severe_conditions (
    manual_id TEXT NOT NULL, no INT NOT NULL, page INT, condition TEXT,
    PRIMARY KEY (manual_id, no)
);
CREATE TABLE IF NOT EXISTS my_maintenance (
    id SERIAL PRIMARY KEY,
    manual_id TEXT NOT NULL, item TEXT NOT NULL, done_date DATE NOT NULL, mileage INT,
    UNIQUE (manual_id, item, done_date)  -- R-7 같은 항목·같은 날짜는 한 번만
);
"""


def make_tables():
    with connect() as conn:
        conn.execute(TABLES)
