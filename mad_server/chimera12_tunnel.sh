HOST=chimera12
PORT=8012

ssh -J haehn@monster.verymad.net \
  daniel.haehn@chimera.umb.edu \
  -L "${PORT}:${HOST}:${PORT}" \
  -N
