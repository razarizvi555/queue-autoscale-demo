FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py .
USER 1000
EXPOSE 9100
CMD ["python", "-u", "worker.py"]
