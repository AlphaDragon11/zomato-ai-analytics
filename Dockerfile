# 1. Use a lightweight official Python image
FROM python:3.10-slim

# 2. Set the working directory inside the container
WORKDIR /app

# 3. Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. Copy all your application code and folders into the container
COPY . .

# 5. Expose the port Streamlit uses
EXPOSE 8501

# 6. Run the Streamlit app
# Note: --server.address 0.0.0.0 is CRITICAL for Docker to allow external access
CMD ["streamlit", "run", "app.py", "--server.port", "8501", "--server.address", "0.0.0.0"]