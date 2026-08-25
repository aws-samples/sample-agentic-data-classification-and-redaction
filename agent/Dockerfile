FROM --platform=linux/arm64 python:3.13-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy agent code
COPY agent.py .

# AgentCore Runtime requires port 8080
EXPOSE 8080

CMD ["python", "agent.py"]
