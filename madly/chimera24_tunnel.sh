HOST=chimera24 PORT=8000 && ssh haehn@monster.verymad.net -L $PORT:*:$PORT ssh daniel.haehn@chimera.umb.edu -L $PORT:$HOST:$PORT -N
