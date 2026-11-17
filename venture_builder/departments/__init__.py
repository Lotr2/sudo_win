"""Departments: one module per phase. Each exposes NODES: {node name: step function}.
A step takes (ctx, answer) and returns Next(node) or Ask(...)."""
