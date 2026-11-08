{% extends "stages/_base.md" %}
{% set title = "SPECKIT IMPLEMENT" %}
{% set skill = "speckit-implement" %}
{% block body %}
{% include "stages/_conventions.md" %}
Implement `{{ spec_path }}/tasks.md` in order, test-first. Change each finished task's `- [ ]` to `- [x]`. Tasks that only run tests, lint or typecheck are the bot's job after you finish; skip them. Otherwise leave no task unticked: if one truly cannot be done, stop and say why in your reply.
{%- endblock %}
