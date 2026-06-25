Deployment patch

Replace these files in the repository root:
- requirements.txt
- runtime.txt
- render.yaml
- Procfile

If deployment still fails because of missing Python packages,
the application likely imports additional libraries that must
be added to requirements.txt.
