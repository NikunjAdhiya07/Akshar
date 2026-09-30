FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY fonts ./fonts
COPY samples ./samples
COPY run.py .
COPY supabase ./supabase

ENV AKSHAR_HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["python", "run.py"]
