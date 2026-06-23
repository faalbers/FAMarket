---
name: make_filters
description: This skill only gets triggered with /make_filters command. ask me to continue with this skill before going ahead.
---

# make_filters

This skill will create valid FAMarket `.filt` filter set from plain-English instructions that you only read in dev_docs/create_filters.md
It will also create a report at the end about the created filters with
Follow the Procedure steps explained below

## Purpose

Create FAMarket filters that I can use to find stocks in the database universe.

## When to use

it is very clearly explained in the description:

## When NOT to use

if it the prompt did NOT use the skill command make_filters.

## Inputs you need from the user

List what you must know before producing output, and what to ask for if missing.
- go through each filter you want o create one by one and do the following things before creating them
- make sure you give me the name and a comprehensible brief of what each filter does you are about to create.
- becore creating the filter, ask me the options to save it or chat about it first.

## Reference: how filters work in this repo

First you need to prepare yourself with the following steps:
- Pull the real details from the codebase so the skill stays accurate.
- You will first figure out all the filter system in the project and all the ways you can create filters using available filter parameters.
- You will figure out what all these filter parameters mean and what they represent.
- You will look at the analysis_layer scoring_rules how to interprete these filters well.
- You will also have a total understanding on the Category Scores and how they are created and how they are used in the filter system.
- Block model + `.filt` JSON: `ui/filter_engine.py`
- Per-`screen_type` metric applicability: `ui/filter_registry.py`
- Filter variants (Value / vs Sector / vs Industry / Score): Make sure to use these if it makes sense per instructions
- Where filters are saved: `settings.FILTERS_DIR`

## End Report

After creating the filters, create a full report that replaces or creates a file under dev_docs/filters_report.md with the following information:
- List of all the filters created with their names
- Add a description for each filter explaining what your thinking pattern was during creation.
- Add a list of parameters that i can sort to find the best results from that filter and how I should interprete these parameters.

## Procedure

1. Go through the reference so you get up to date first on what you need to know.
2. Read the plain-English instructions that you only read in dev_docs/create_filters.md to create the filters
3. Find all the stock market analysis knowledge you can find with web search to get the best results on the instructions in create_filters.md
4. Go through Inputs you need from the user explained above before creating the filters
5. Validate / save the filters using a version suffix if they already exist/ report using the End Report explained above.

