FROM python:3.10-slim

# Add OpenContainers labels
LABEL org.opencontainers.image.source=https://github.com/Pukabyte/autosync
LABEL org.opencontainers.image.description="Autosync"
LABEL org.opencontainers.image.licenses=MIT

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY *.py .
COPY templates/ ./templates/
COPY static/ ./static/

# Expose port
EXPOSE 3536

# Run the application
CMD ["python", "main.py"]
