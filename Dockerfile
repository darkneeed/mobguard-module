FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY mobguard_module ./mobguard_module
COPY mobguard-module.py ./
CMD ["python", "mobguard-module.py"]
