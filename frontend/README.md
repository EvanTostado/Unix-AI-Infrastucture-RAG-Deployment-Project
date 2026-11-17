# Python Troubleshooting Chatbot — Frontend Prototype

This folder contains a frontend prototype built with plain HTML, CSS, and JavaScript. It demonstrates a Python troubleshooting interface using one prewritten TypeError example.

**This prototype does not include a backend. The AI app is not connected.** There are no database connections or external API requests, and submitted Python code is never executed.

## What is included

- A question field for explaining what you need help with.
- A code field that preserves indentation and line breaks.
- An error field for a traceback or error message.
- A **Load example** button that fills in the supported example: `"Age: " + 25`.
- A submit button with input validation and a brief loading state.
- A sample answer explaining the error's meaning, cause, fix, and reasoning.
- A sources area clearly labeled as sample content; document retrieval is not connected.
- A sample-mode notice explaining that no live AI is connected.

Only the built-in example is supported. Other code or errors receive an unsupported-input message, rather than a diagnosis. The question field does not trigger analysis; the explanation is prewritten.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Defines the page structure, input fields, buttons, notices, answer area, and sources area. |
| `styles.css` | Controls colors, spacing, responsive layout, and code formatting. |
| `app.js` | Handles loading the example, validating input, showing the loading state, and displaying the response. |
| `sample-responses.js` | Stores the known example, matches supported input, and returns the prewritten response. This logic is separate so it can later be replaced with a backend request. |
| `README.md` | Explains the prototype, its files, limitations, and preview instructions. |

## Run the preview with Live Preview

1. Open this repository in Visual Studio Code.
2. Install the **Live Preview** extension by Microsoft if it is not already installed.
3. Open `frontend/index.html`.
4. Right-click inside the editor and select **Show Preview** from the Live Preview extension.
5. Click **Load example**, then **Show sample explanation** to try the supported example.

Live Preview serves the frontend files locally. You do not need to start Docker, Ollama, or any services in `ai-tier/` to use this prototype.
