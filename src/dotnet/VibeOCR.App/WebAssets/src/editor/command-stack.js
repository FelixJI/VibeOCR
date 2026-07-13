const clone = (value) => structuredClone(value);

export class CommandStack {
  #entries = [];
  #cursor = 0;

  constructor(limit = 50) {
    if (!Number.isInteger(limit) || limit < 1) throw new RangeError("Command limit must be positive");
    this.limit = limit;
  }

  get canUndo() { return this.#cursor > 0; }
  get canRedo() { return this.#cursor < this.#entries.length; }

  execute(command, state) {
    validateCommand(command);
    this.#entries.splice(this.#cursor);
    this.#entries.push(clone(command));
    if (this.#entries.length > this.limit) this.#entries.shift();
    this.#cursor = this.#entries.length;
    return applyCommand(state, command, false);
  }

  undo(state) {
    if (!this.canUndo) return clone(state);
    this.#cursor -= 1;
    return applyCommand(state, this.#entries[this.#cursor], true);
  }

  redo(state) {
    if (!this.canRedo) return clone(state);
    const next = applyCommand(state, this.#entries[this.#cursor], false);
    this.#cursor += 1;
    return next;
  }

  serialize() { return { limit: this.limit, cursor: this.#cursor, entries: clone(this.#entries) }; }

  static hydrate(value) {
    const stack = new CommandStack(value?.limit);
    if (!Array.isArray(value.entries) || !Number.isInteger(value.cursor) || value.cursor < 0 || value.cursor > value.entries.length) {
      throw new TypeError("Invalid serialized command stack");
    }
    value.entries.forEach(validateCommand);
    stack.#entries = clone(value.entries);
    stack.#cursor = value.cursor;
    return stack;
  }
}

function validateCommand(command) {
  if (!command || typeof command !== "object" || !["add", "remove", "move", "resize", "property", "text", "rotate", "crop"].includes(command.kind)) {
    throw new TypeError("Unsupported editor command");
  }
}

export function applyCommand(state, command, reverse = false) {
  if ((command.kind === "rotate" || command.kind === "crop") && command.beforeState && command.afterState) {
    return clone(reverse ? command.beforeState : command.afterState);
  }
  const next = clone(state);
  const items = next.annotations ?? (next.annotations = []);
  if (command.kind === "add") {
    if (reverse) next.annotations = items.filter((item) => item.id !== command.annotation.id);
    else items.push(clone(command.annotation));
    return next;
  }
  if (command.kind === "remove") {
    if (reverse) items.splice(Math.min(command.index ?? items.length, items.length), 0, clone(command.annotation));
    else next.annotations = items.filter((item) => item.id !== command.annotation.id);
    return next;
  }
  if (command.kind === "rotate" || command.kind === "crop") {
    next.image[command.kind === "rotate" ? "rotation" : "crop"] = clone(reverse ? command.from : command.to);
    return next;
  }
  const item = items.find((entry) => entry.id === command.id);
  if (!item) throw new RangeError("Annotation does not exist");
  const value = clone(reverse ? command.from : command.to);
  if (["move", "resize"].includes(command.kind)) item.rect = value;
  else if (command.kind === "text") item.text = value;
  else item[command.property] = value;
  return next;
}
