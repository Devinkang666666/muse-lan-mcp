# Security Policy

## Supported Versions

Only the latest version in the `main` branch is supported. Older
snapshots are not maintained.

## Private LAN Only

This server is designed for a private home LAN only:

- It must never be exposed to the public internet without a strong
  Bearer token (`MUSE_TOKEN`).
- A tunnel (for example ngrok) must be stopped when you are done.
  Do not leave it running long-term.
- Public IP addresses are rejected by design. Hostnames are allowed
  only when they resolve to a private or loopback address.

## After Login, Read-Only

`auth_fetch` logs in and then does one read-only GET. It changes no
device settings. Do not use `Set*` endpoints, and do not share a
tunnel address, Bearer token, login password, or Cookie value with
anyone.

## Reporting a Vulnerability

If you find a security issue, please report it through GitHub Issues
without posting any real credential, password, Cookie value, tunnel
domain, or device address. Describe the issue, the version, and how
to reproduce it in general terms, and the maintainer will respond
as soon as possible.

Do not open a public issue that contains a working exploit against
a real home network, or any private information about your own or
someone else's devices.
