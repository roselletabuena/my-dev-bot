{% extends "stages/_base.md" %}
{% set title = "SPECKIT PLAN" %}
{% set skill = "speckit-plan" %}
{% block body %}
{% include "stages/_conventions.md" %}
Produce the architecture plan for `{{ spec_path }}/spec.md`: `{{ spec_path }}/plan.md`, `{{ spec_path }}/data-model.md`, `{{ spec_path }}/contracts/` and `{{ spec_path }}/quickstart.md`. Respect the repo's folder structure and constitution gates; resolve every open question in the spec as a recorded decision.
{%- endblock %}
