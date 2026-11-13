// Local sample provider. A future backend adapter can implement the same
// async respond({ question, code, error }) contract without changing rendering.
// This provider never executes code or makes network requests.
window.sampleResponses = (() => {
  const example = Object.freeze({
    question: 'Why does combining this text and number produce a TypeError?',
    code: '"Age: " + 25',
    error: 'TypeError: can only concatenate str (not "int") to str'
  });

  async function respond({ code, error }) {
    // Only outer whitespace and line-ending differences are tolerated.
    // A traceback is accepted when its final line is exactly the known error.
    const normalizedCode = code.replace(/\r\n/g, '\n').trim();
    const finalErrorLine = error.trim().split(/\r?\n/).pop().trim();
    if (normalizedCode !== example.code || finalErrorLine !== example.error) {
      return {
        supported: false,
        message: 'This input is not supported in sample mode. No diagnosis was performed. Choose “Load example” to view the one prewritten TypeError explanation.'
      };
    }
    return {
      supported: true,
      sections: [
        { title: 'Meaning', text: 'A TypeError means an operation received a value of an incompatible type. This example tries to join a string and an integer.' },
        { title: 'Cause', text: 'In "Age: " + 25, "Age: " is a string and 25 is an integer. String concatenation with + requires another string; Python does not automatically convert the integer here.' },
        { title: 'Fix', text: 'Convert the number to a string, or format it into a string with an f-string.', code: '"Age: " + str(25)\n# Or:\nf"Age: {25}"' },
        { title: 'Reasoning', text: 'str(25) produces "25", so + joins two strings. An f-string formats the value directly into the text. Both expressions produce "Age: 25". This explanation is prewritten; neither expression has been executed.' }
      ]
    };
  }
  return Object.freeze({ example, respond });
})();
