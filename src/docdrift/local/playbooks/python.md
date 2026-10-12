## Language-specific guidance: Python

- List public functions and classes under "Public interface" with their signatures
  (the structural analysis and the outline show them exactly - copy, do not guess).
- Mention decorators that carry meaning (`@dataclass`, `@click.command`); omit
  purely stylistic ones.
- Use type hints as the basis for interface descriptions.
- Mention private functions (leading underscore) only when they are essential to
  understand the module.
