FROM python:3.14-slim

RUN useradd -m -u 1000 sandboxuser
COPY runner.py /runner.py
RUN chmod 555 /runner.py

USER sandboxuser
WORKDIR /
ENTRYPOINT ["python", "/runner.py"]
