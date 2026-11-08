{% extends "stages/_base.md" %}
{% set title = "SPECKIT AMEND PLAN" %}
{% set skill = "speckit-plan" %}
{% block body %}
{% include "stages/_conventions.md" %}
The spec in `{{ spec_path }}/spec.md` was just amended for this ticket. Update only the affected parts of `{{ spec_path }}/plan.md`, `{{ spec_path }}/data-model.md`, `{{ spec_path }}/contracts/` and `{{ spec_path }}/quickstart.md` to match; leave the rest alone.
{%- endblock %}
