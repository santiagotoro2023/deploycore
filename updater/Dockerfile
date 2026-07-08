FROM docker:27-cli

RUN apk add --no-cache bash git postgresql-client jq curl

WORKDIR /
COPY update.sh /update.sh
RUN chmod +x /update.sh

CMD ["/update.sh"]
