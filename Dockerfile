FROM ghcr.io/sigstore/cosign/cosign@sha256:d91bc4e7e95e8d2f549c747a72dc174f90579e410a1695f57f686674f84ce849 AS cosign
FROM ghcr.io/astral-sh/uv@sha256:85d4cb1afa769a7338e095b927bee941cf5ec92266c7424b3f6c0f2748567248

COPY --from=cosign /ko-app/cosign /usr/local/bin/cosign
COPY trust/v9-grounded.pub /trust/v9-grounded.pub
COPY bootstrap.py /usr/local/bin/v9-bootstrap

RUN chmod 0555 /usr/local/bin/cosign /usr/local/bin/v9-bootstrap /trust/v9-grounded.pub
ENTRYPOINT ["python", "/usr/local/bin/v9-bootstrap"]
