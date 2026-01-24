# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for CatBoost
RUN apt-get update && apt-get install -y \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Create a non-root user with an explicit UID and add permission to access the /app folder
# This prevents potential container escapes by running the application as a restricted user
RUN adduser -u 5678 --disabled-password --gecos "" appuser

# Create the SQLite DB file and set permissions so appuser can write to it
RUN touch /app/autosafe.db && chown appuser:appuser /app/autosafe.db

# Switch to the non-root user
USER appuser

# Expose port 8000
EXPOSE 8000

# Define environment variable
ENV PORT=8000

# Run the application
# Run the application using the PORT environment variable provided by Railway
CMD uvicorn main:app --host 0.0.0.0 --port $PORT --workers 4
