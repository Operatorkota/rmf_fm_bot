 python:3.11-slim-buster
   2 WORKDIR /app
   3 COPY requirements.txt .
   4 RUN pip install --no-cache-dir -r requirements.txt
   5 COPY . .
   6 CMD ["python", "bot.py"]