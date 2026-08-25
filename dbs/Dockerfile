FROM python:3.11-slim

ARG DBS_MCP_REPO=https://github.com/juanpablosalas/mcp-servers.git

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        libcurl4-openssl-dev \
        libssl-dev \
        libffi-dev \
        ca-certificates \
        igtf-policy-classic \
        voms-clients-java \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /etc/vomses \
    && echo '"cms" "voms-cms-auth.cern.ch" "443" "/DC=ch/DC=cern/OU=computers/CN=cms-auth.cern.ch" "cms"' \
       > /etc/vomses/cms

RUN mkdir -p /etc/grid-security/vomsdir/cms \
    && printf '/DC=ch/DC=cern/OU=computers/CN=cms-auth.cern.ch\n/DC=ch/DC=cern/CN=CERN Grid Certification Authority\n' \
       > /etc/grid-security/vomsdir/cms/voms-cms-auth.cern.ch.lsc

WORKDIR /app

COPY pyproject.toml .
COPY README.md .
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8013

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8013

COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]