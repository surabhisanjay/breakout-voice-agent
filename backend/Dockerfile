FROM python:3.11-slim

WORKDIR /workspace

# Install dependencies
COPY requirements.txt /workspace/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy codebase
COPY . /workspace

# Set Python path
ENV PYTHONPATH=/workspace
