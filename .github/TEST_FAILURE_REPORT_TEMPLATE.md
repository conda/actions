---
title: '{{ env.TITLE }} ({{ date | date("YYYY-MM-DD") }})'
labels: ['type::bug', 'type::testing', 'source::auto']
---

The {{ workflow }} workflow failed on {{ date | date("YYYY-MM-DD HH:mm") }} UTC

Full run: {{ repo.html_url }}/actions/runs/{{ env.RUN_ID }}
