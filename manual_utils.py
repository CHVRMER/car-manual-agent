"""설명서 PDF 처리: 해시, 쪽별 글자, 조각, 점검표 쪽 후보, 쪽 이미지."""
import hashlib
import io
import re

import pypdfium2 as pdfium
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def file_hash(data: bytes) -> str:
    """같은 파일이면 같은 값. 중복 업로드 확인과 manual_id로 사용"""
    return hashlib.sha256(data).hexdigest()[:16]


def read_pages(data: bytes) -> list[dict]:
    """쪽별 글자. 강의 코드의 pypdf는 일부 기아 설명서에서 한글이 깨져서 pypdfium2를 씀"""
    pdf = pdfium.PdfDocument(data)
    pages = []
    for i in range(len(pdf)):
        text = pdf[i].get_textpage().get_text_range().replace("\r", "")
        pages.append({"page": i + 1, "text": text})
    return pages


def to_chunks(pages: list[dict], manual_id: str, size: int = 500, overlap: int = 100):
    """쪽마다 나눠 조각을 만듦. 메타데이터에 manual_id와 PDF 쪽 번호"""
    splitter = RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    docs, ids = [], []
    for p in pages:
        if not p["text"].strip():
            continue
        for j, t in enumerate(splitter.split_text(p["text"])):
            docs.append(Document(page_content=t, metadata={"manual_id": manual_id, "page": p["page"]}))
            ids.append(f"{manual_id}-{p['page']}-{j}")
    return docs, ids


def _score(text: str) -> int:
    t = re.sub(r"\s", "", text)
    marks = t.count("○") + t.count("●")
    keys = sum(k in t for k in ("점검주기", "가혹조건", "점검항목", "통상조건"))
    return marks + 5 * keys


def find_schedule_pages(pages: list[dict]) -> tuple[int, int]:
    """점검표 쪽 후보 (시작, 끝). 화면에서 사용자가 고칠 수 있는 초기값으로만 씀"""
    scores = {p["page"]: _score(p["text"]) for p in pages}
    best = max(scores, key=scores.get)
    start = end = best
    while scores.get(start - 1, 0) >= 10:
        start -= 1
    while scores.get(end + 1, 0) >= 10:
        end += 1
    return start, end


def render_page_png(data: bytes, page: int, scale: float = 2.0) -> bytes:
    """PDF 쪽 번호(1부터) → PNG 바이트. 점검표 읽기와 근거 이미지에 같이 씀"""
    pdf = pdfium.PdfDocument(data)
    img = pdf[page - 1].render(scale=scale).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
