'use strict';
(function () {
  var canvas = document.getElementById('hearth');
  var expression = new URLSearchParams(window.location.search).get('expression') || 'idle';
  if (!canvas || !window.TraceFace) return;
  window.TraceFace.attach(canvas);
  window.TraceFace.setReduced(false);
  window.TraceFace.setState(expression);
}());
