# Dify 1.9.2 always requests sandbox network access even for JSON-only Code nodes.
# Keep our sandbox network disabled and make the API request match that restriction.
FROM langgenius/dify-api:1.9.2
RUN python -c 'from pathlib import Path; p=Path("/app/api/core/helper/code_executor/code_executor.py"); s=p.read_text(); old="\"enable_network\": True,"; assert s.count(old)==1, "Pinned Dify CodeExecutor changed; review compatibility patch"; p.write_text(s.replace(old,"\"enable_network\": False,"))'
