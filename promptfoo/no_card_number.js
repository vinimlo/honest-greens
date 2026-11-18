// D7: a grader of the user's own that is down (it would call a service that refuses).
module.exports = (output, context) => {
  throw new Error('grader unavailable: connection refused');
};
