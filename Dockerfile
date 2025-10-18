FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y gcc python3-dev libffi-dev libpq-dev make && \
    rm -rf /var/lib/apt/lists/*

# Copy only requirements.txt first
COPY requirements.txt .

# Upgrade pip to latest compatible version
RUN pip install --upgrade "pip<24.1"

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Run the app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8009", "--loop", "uvloop", "--http", "httptools"]




# FROM python:3.12-slim

# WORKDIR /app

# # Install system dependencies
# RUN apt-get update && \
#     apt-get install -y gcc python3-dev libffi-dev libpq-dev && \
#     rm -rf /var/lib/apt/lists/*

# # Copy only requirements.txt first to leverage Docker cache for pip installations
# COPY requirements.txt .

# # Upgrade pip and install dependencies with trusted hosts
# RUN pip install --upgrade pip
# RUN pip install --no-cache-dir --trusted-host pypi.org --trusted-host pypi.python.org --trusted-host=files.pythonhosted.org -r requirements.txt

# # Copy the rest of the application
# COPY . .

# # Command to run the application
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
