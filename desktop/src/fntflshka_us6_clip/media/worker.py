import logging
from dataclasses import replace
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal, Slot
from .image import load_image, prepare_image, SceneSource, scene_canvas,EditorRenderRequest
from .animation import GifSource, load_gif, prepare_gif, prepare_cached
from ..core.asset import parse_asset
from ..problems import problem

log = logging.getLogger(__name__)


class ImageWorker(QObject):
    ready = Signal(int, object)
    failed = Signal(int, str)
    failure = Signal(int, object)
    decoded = Signal(object, object)
    project_saved=Signal(str)
    project_loaded=Signal(str,object)
    fill_ready=Signal(object)

    def __init__(self):
        super().__init__()
        self.path = None
        self.source = None
        self.text_base_key=None;self.text_base=None

    @Slot(object, bytes)
    def decode_preview(self, key, data):
        from .animation import preview_frames
        try:
            self.decoded.emit(key, preview_frames(data))
        except Exception as error:
            log.exception("Animation cache preview failed")
            self.failure.emit(-1,problem(error,'preview',context={'request':key}))

    @Slot(int, object, object)
    def render(self, generation, path, placement):
        if QThread.currentThread().isInterruptionRequested():
            return
        try:
            preview_only=isinstance(placement,EditorRenderRequest) and placement.preview_only
            if isinstance(placement,EditorRenderRequest):placement=placement.placement
            if preview_only:placement=replace(placement,text_motion='static')
            if isinstance(path, GifSource):
                stat = path.path.stat()
                key = ("gif", path.path, stat.st_mtime_ns, stat.st_size, path.mode, path.first, path.last, path.step, path.optimize)
                if self.path != key:
                    self.source, self.path = load_gif(path.path, path), key
                self.ready.emit(generation, prepare_gif(self.source, replace(path, first=0, last=0, step=1), placement))
                return
            if isinstance(path, SceneSource) and parse_asset(path.asset).get("type") == 2:
                self.ready.emit(generation, prepare_cached(path, placement))
                return
            if isinstance(path, SceneSource):
                if self.path != path:
                    self.source, self.path = scene_canvas(path), path
                    log.info("Editing cached scene pixels size=%s", self.source.size)
            elif Path(path).suffix.lower()=='.fclipimage':
                from .project import load_project
                image_path=Path(path);stat=image_path.stat()
                key=(image_path,stat.st_mtime_ns,stat.st_size)
                if self.path!=key:self.source,_=load_project(image_path);self.path=key
            else:
                image_path = Path(path)
                stat = image_path.stat()
                cache_key = (image_path, stat.st_mtime_ns, stat.st_size)
                if self.path != cache_key:
                    source = load_image(image_path)
                    self.source, self.path = source, cache_key
                    log.info("Image imported %s size=%s", path, source.size)
            base_placement=replace(placement,text_layers=(),text_motion='static',text_speed=40,text_pause=0,paint_pixels=b'')
            base_key=(self.path,base_placement)
            if placement.text_layers or placement.paint_pixels:
                from .text import prepare_text
                if self.text_base_key!=base_key:
                    self.text_base=prepare_image(self.source,base_placement);self.text_base_key=base_key
                if placement.text_layers:result=prepare_text(self.source,placement,base=self.text_base)
                else:
                    from .drawing import prepare_paint
                    result=prepare_paint(self.text_base,placement)
            else:
                result=prepare_image(self.source,placement)
                self.text_base,self.text_base_key=result,base_key
            if preview_only:result=replace(result,preview_only=True)
            self.ready.emit(generation, result)
        except Exception as error:
            log.exception("Image preparation failed")
            self.failure.emit(generation,problem(error,'image',context={
                'source_path':str(path.path) if isinstance(path,GifSource) else str(path) if isinstance(path,(Path,str)) else 'verified RAM scene',
                'source_type':type(path).__name__,'generation':generation}))
            message = str(error) if isinstance(error, ValueError) else "Не удалось открыть картинку. Выбери другой файл."
            self.failed.emit(generation, message)

    @Slot(object)
    def fill(self,request):
        from .drawing import flood
        try:self.fill_ready.emit((request['key'],request['revision'],flood(request['pixels'],request['target'],request['point'],request['color'])))
        except Exception as error:
            log.exception('Drawing fill failed')
            self.failure.emit(-3,problem(error,'image',context={'action':'paint_fill','revision':request['revision'],'key':request['key']}))

    @Slot(object)
    def project(self,request):
        from .project import load_project,save_project
        action,path,source,placement=request
        try:
            if action=='load':
                pixels,p=load_project(path)
                stat=Path(path).stat();self.source=pixels;self.path=(Path(path),stat.st_mtime_ns,stat.st_size)
                self.project_loaded.emit(str(path),p)
            else:
                if isinstance(source,SceneSource):pixels=scene_canvas(source)
                elif isinstance(self.path,tuple) and self.path[0]==Path(source) and self.source is not None:pixels=self.source
                elif Path(source).suffix.lower()=='.fclipimage':pixels,_=load_project(source)
                else:pixels=load_image(Path(source))
                save_project(path,pixels,placement);self.project_saved.emit(str(path))
        except Exception as error:
            log.exception('Image project failed')
            self.failure.emit(-2,problem(error,'image',context={'source_path':str(path),'action':action}))
