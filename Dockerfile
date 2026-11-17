FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
RUN pip install --no-cache-dir --no-deps -e . && mkdir -p /app/runs && chown -R 1000:1000 /app/runs
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["venture", "serve", "--host", "0.0.0.0", "--port", "8000"]
