# Use an official Python runtime as a parent image
# Updated from 3.9 (EOL Oct 2025) to 3.12 for security patches
FROM python:3.12-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for CatBoost and health checks
RUN apt-get update && apt-get install -y \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
# SECURITY: Running as root allows full system access if container is compromised
RUN useradd -m -u 1000 -s /bin/bash appuser

# Copy the requirements file into the container
COPY --chown=appuser:appuser requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Expose port 8000
EXPOSE 8000

# Define environment variable
ENV PORT=8000

# Health check - verify the application is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Run the application using the PORT environment variable provided by Railway
# Workers configurable via environment variable (default 4)
CMD uvicorn main:app --host 0.0.0.0 --port $PORT --workers ${WORKERS:-4}
