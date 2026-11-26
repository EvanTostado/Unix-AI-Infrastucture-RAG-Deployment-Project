// Interface behavior only: sample matching and content live in the provider.
const form = document.querySelector('#help-form');
const fields = ['question', 'code', 'error'].map(id => document.getElementById(id));
const loadButton = document.querySelector('#load-example');
const submitButton = document.querySelector('#submit');
const panel = document.querySelector('#response-panel');
const status = document.querySelector('#status');
const answer = document.querySelector('#answer');
const validation = document.querySelector('#validation');

loadButton.addEventListener('click', () => {
  fields.forEach(field => { field.value = window.sampleResponses.example[field.id]; });
  validation.textContent = '';
  answer.replaceChildren();
  status.textContent = 'Example loaded. Submit to request an explanation.';
  fields[0].focus();
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  validation.textContent = '';
  const emptyField = fields.find(field => !field.value.trim());
  if (emptyField) {
    validation.textContent = 'Please complete the question, code, and error fields.';
    emptyField.focus();
    return;
  }
  const input = Object.fromEntries(fields.map(field => [field.id, field.value]));
  answer.replaceChildren();
  panel.setAttribute('aria-busy', 'true');
  submitButton.disabled = true;
  loadButton.disabled = true;
  fields.forEach(field => { field.readOnly = true; });
  submitButton.textContent = 'Loading...';
  status.textContent = 'Sending your request to the server...';
  try {
    const response = await fetch('/api/ask', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(input)
    });

    const result = await response.json();

    status.textContent = result.message ||
      (response.ok ? 'Explanation received.' : 'The request failed.');

    for (const section of result.sections || []) {
      const heading = document.createElement('h3');
      heading.textContent = section.title;
      const paragraph = document.createElement('p');
      paragraph.textContent = section.text;
      answer.append(heading, paragraph);
      if (section.code) {
        const pre = document.createElement('pre');
        const code = document.createElement('code');
        code.textContent = section.code;
        pre.append(code);
        answer.append(pre);
      }
    }
  } catch {
    status.textContent = 'Could not retrieve a response. Please try again.';
  } finally {
    panel.setAttribute('aria-busy', 'false');
    submitButton.disabled = false;
    loadButton.disabled = false;
    fields.forEach(field => { field.readOnly = false; });
    submitButton.textContent = 'Get explanation';
  }
});
