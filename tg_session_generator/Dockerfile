FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir telethon

COPY generate_session.py .

CMD ["python", "generate_session.py"]
