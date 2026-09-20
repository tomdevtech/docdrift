## Language-specific guidance: Python

- List public functions and classes under "Public interface", including their
  signatures (name, parameters, and return type when annotated).
- Mention decorators with functional meaning, such as `@dataclass` and
  `@click.command`, but omit purely stylistic decorators.
- Use type hints as the basis for interface descriptions instead of guessing.
- Mention private functions and attributes with a leading underscore only when
  they are essential to understanding the module; do not list all of them.
