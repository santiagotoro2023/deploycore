FROM coturn/coturn:4.6

# getent (for resolving TURN_HOST to an IP - see entrypoint.sh) is already
# present in this base image's libc; nothing extra to install.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
