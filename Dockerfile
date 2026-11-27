# car-manual-agent: 강의 9회차와 같은 방식 (python:3.12-slim, Streamlit 8501)
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# manuals/ (설명서 원본 PDF)는 이미지에 넣지 않고 실행할 때 서버 폴더를 연결한다 (-v)
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
