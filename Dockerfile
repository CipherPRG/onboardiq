# Judge mode: one command (docker compose up) gives the web page, a real SNMP agent to onboard, and the tests.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 OIQ_IN_CONTAINER=1 OIQ_MIB_DIR=/app/mibs_json PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends snmpd snmp libsnmp-base stress-ng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Only the open IETF / net-snmp MIBs are converted into the image; vendor MIBs are never bundled.
RUN python scripts/intake.py /usr/share/snmp/mibs --out /app/mibs_json \
      --only HOST-RESOURCES-MIB,UCD-SNMP-MIB,LM-SENSORS-MIB
# The agent being monitored: this container's own net-snmp, read-only, reachable from inside only.
RUN printf 'agentaddress udp:127.0.0.1:161\nrocommunity public 127.0.0.1\nsysLocation OnboardIQ judge container\n' > /etc/snmp/snmpd.conf
RUN chmod +x docker/entrypoint.sh
EXPOSE 8765
ENTRYPOINT ["docker/entrypoint.sh"]
