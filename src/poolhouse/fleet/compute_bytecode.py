"""Installer-only removal of caches for verified managed Python sources."""

CLEANUP = '''import importlib.util,os,stat
assert sys.prefix!=sys.base_prefix, 'Compute cache cleanup requires an isolated environment'
assert site.is_relative_to(Path(sys.prefix)), 'Compute installation is outside its interpreter'
def directory(parts, start):
 current=os.dup(start)
 try:
  for part in parts:
   following=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=current)
   os.close(current)
   current=following
  return current
 except BaseException:
  os.close(current)
  raise
anchor=os.open(site.anchor,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 root=directory(site.parts[1:],anchor)
finally:
 os.close(anchor)
try:
 for name in (m['base_files'] | m['files']):
  if not name.endswith('.py'):
   continue
  source=Path(d.locate_file(name))
  for optimization in ('','1','2'):
   relative=Path(importlib.util.cache_from_source(str(source),optimization=optimization)).relative_to(site)
   try:
    parent=directory(relative.parts[:-1],root)
   except FileNotFoundError:
    continue
   try:
    try:
     before=os.stat(relative.name,dir_fd=parent,follow_symlinks=False)
    except FileNotFoundError:
     continue
    assert stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_uid==os.getuid(), 'Compute cache is not an owned regular file'
    cache=os.open(relative.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    try:
     opened=os.fstat(cache)
     current=os.stat(relative.name,dir_fd=parent,follow_symlinks=False)
     assert (before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino), 'Compute cache changed during cleanup'
     os.unlink(relative.name,dir_fd=parent)
    finally:
     os.close(cache)
   finally:
    os.close(parent)
finally:
 os.close(root)
'''
