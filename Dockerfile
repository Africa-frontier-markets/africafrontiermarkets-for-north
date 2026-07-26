# Dockerfile for afm-frontier-markets project
# This Dockerfile expects the uploaded zip file to exist in the repo root
# It will unzip the archive during image build into /app, install dependencies
# from a detected requirements.txt or by installing the package if pyproject/setup exists.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install unzip and build deps (kept minimal)
RUN apt-get update \
    && apt-get install -y --no-install-recommends unzip build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the zip into the image (the repo currently contains the zip file)
# Use the JSON-array form for COPY so filenames with spaces/parentheses are handled correctly
# Updated to reference the renamed archive without spaces/parentheses
COPY ["afm-frontier-markets-fixed-1.zip", "./project.zip"]

# Unzip into /app and move contents up if needed
RUN unzip project.zip -d /app || true \
    && if [ -d ./afm-frontier-markets-fixed ]; then mv ./afm-frontier-markets-fixed/* . || true; fi \
    && rm -f project.zip

# Install Python deps if requirements.txt exists
RUN if [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    elif [ -f ./requirements.txt ]; then \
        pip install --no-cache-dir -r ./requirements.txt; \
    elif [ -f pyproject.toml ] || [ -f setup.py ]; then \
        pip install --no-cache-dir .; \
    else \
        echo "No requirements.txt or setup.py/pyproject.toml found; you may need to adjust the Dockerfile to install dependencies."; \
    fi

# Expose a common port (change if your app listens on a different port)
EXPOSE 8000

# Default cmd: try to run api_gateway.main (common entrypoint in the repo), otherwise open a shell
# Update this CMD to the correct startup command for your application (gunicorn/uvicorn/flask/etc.)
CMD ["sh", "-c", "python -m api_gateway.main || python api_gateway/main.py || echo 'No default entrypoint found - dropping to shell' && bash"]
