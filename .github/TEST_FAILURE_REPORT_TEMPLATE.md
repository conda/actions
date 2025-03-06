---
title: '{{ env.TITLE }} ({{ date | date("YYYY-MM-DD") }})'
labels: ['type::bug', 'type::testing', 'source::auto']
---

The {{ workflow }} workflow failed on {{ date | date("YYYY-MM-DD HH:mm") }} UTC

Full run: https://github.com/{{ repo.owner }}/{{ repo.repo }}/actions/runs/{{ env.RUN_ID }}
