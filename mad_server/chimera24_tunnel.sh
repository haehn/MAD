HOST=chimera24
PORT=8000

ssh -J haehn@monster.verymad.net \
  daniel.haehn@chimera.umb.edu \
  -L "${PORT}:${HOST}:${PORT}" \
  -N
