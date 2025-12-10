# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose port 8000
EXPOSE 8000

# Define environment variable
ENV PORT=8000

# Health check for container monitoring
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" || exit 1

# Run the application with optimized settings for Railway (1-2 vCPUs)
# - 2 workers instead of 4 to match available resources
# - keep-alive for connection reuse
# - timeout for slow requests
CMD python build_db.py && uvicorn main:app --host 0.0.0.0 --port $PORT --workers 2 --timeout-keep-alive 30

