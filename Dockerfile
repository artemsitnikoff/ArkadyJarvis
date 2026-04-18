FROM python:3.11-slim

# Node.js + Claude CLI for resume scoring (Recruiter Anatoly)
# ffmpeg for Socrates meeting pipeline (video/audio → opus conversion)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g @anthropic-ai/claude-code && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY . .

RUN mkdir -p data

EXPOSE 8002

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
