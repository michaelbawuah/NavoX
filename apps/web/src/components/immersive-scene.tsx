"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./navox-cinematic.module.css";

const vertex = `attribute vec2 position;void main(){gl_Position=vec4(position,0.,1.);}`;
const fragment = `
precision highp float;
uniform vec2 resolution;
uniform vec2 pointer;
uniform float time;
uniform float mode;
uniform float progress;
mat2 rot(float a){return mat2(cos(a),-sin(a),sin(a),cos(a));}
float ease(float a,float b,float x){return smoothstep(a,b,x);}
vec4 chapters(){
 float a=ease(.18,.92,progress),b=ease(1.18,1.92,progress),c=ease(2.18,2.94,progress);
 return vec4(1.-a,a-b,b-c,c);
}
vec3 glowBlue(){
 vec4 w=chapters();
 return vec3(.025,.20,.69)*w.x+vec3(.008,.41,.48)*w.y+vec3(.10,.38,.78)*w.z+vec3(.17,.075,.53)*w.w;
}
vec3 glowViolet(){
 vec4 w=chapters();
 return vec3(.46,.026,.69)*w.x+vec3(.018,.61,.31)*w.y+vec3(.51,.79,.94)*w.z+vec3(.82,.19,.32)*w.w;
}
vec3 lightColor(){return normalize(glowBlue()+glowViolet());}
vec3 ringSpace(vec3 p,float secondary){
 vec4 w=chapters();
 float tilt=dot(w,vec4(2.3,.63,1.54,1.0));
 p.yz=rot(tilt+secondary*1.4+sin(time*.2)*.08)*p.yz;
 p.xy=rot(.36+time*(secondary>.5?-.11:.12)+secondary*.75)*p.xy;
 return p;
}
float ringRadius(){return mix(dot(chapters(),vec4(.82,1.09,.94,.88)),1.14,clamp(mode-1.,0.,1.));}
float octa(vec3 p, vec3 size){p=abs(p)/size;return (p.x+p.y+p.z-1.)*.57735*min(size.x,min(size.y,size.z));}
float rock(vec3 p, vec3 size, float tilt){
 p.xz=rot(tilt)*p.xz;p.xy=rot(tilt*.35)*p.xy;
 float d=octa(p,size);
 d=max(d,-p.y-size.y*.68);
 d=max(d,dot(p,normalize(vec3(.8,.4,-.3)))-size.x*.53);
 d+=.0015*sin(p.x*21.+sin(p.z*13.))*sin(p.y*18.);
 return d;
}
vec3 orbCenter(){return vec3(.08*sin(progress*2.),.17+sin(time*.55)*.055-ease(2.2,3.,progress)*.12,0.);}
vec2 scene(vec3 p){
 float open=ease(.1,1.5,progress);
 float back=ease(1.7,3.,progress);
 vec2 result=vec2(p.y+.78,0.);
 vec3 q=p-orbCenter();q.xz=rot(time*.12+pointer.x*.12)*q.xz;q.xy=rot(.15*sin(time*.2))*q.xy;
 float body=length(q)-(.72+.035*sin(q.x*5.+time*.6)*sin(q.y*4.-time*.4));
 float focus=length(q)-.72;
 body=mix(body,focus,clamp(mode,0.,1.));
 body=mix(body,length(q)-.57,max(0.,mode-1.));
 if(body<result.x)result=vec2(body,1.);
 vec4 w=chapters();
 vec3 ring=ringSpace(p-orbCenter(),0.);
 float radius=ringRadius();
 float thickness=dot(w,vec4(.014,.018,.009,.012));
 float ringD=length(vec2(length(ring.xz)-radius,ring.y))-thickness;
 if(ringD<result.x)result=vec2(ringD,3.);
 float doubleOrbit=w.y+w.w;
 if(doubleOrbit>.002){
  vec3 second=ringSpace(p-orbCenter(),1.);
  float secondD=length(vec2(length(second.xz)-mix(.73,1.18,doubleOrbit),second.y))-.009*doubleOrbit;
  if(secondD<result.x)result=vec2(secondD,4.);
 }
 vec3 r=p-vec3(-2.65-open*.6,-.75+open*1.1-back*.5,-.45-progress*.28);
 float stone=rock(r,vec3(.9,1.9+open*.5,.72),.24+progress*.17);
 r=p-vec3(2.2+open*.8,-.7+open*.9-back*.7,-.5-progress*.2);
 stone=min(stone,rock(r,vec3(.85,2.05,.7),-.3-progress*.18));
 r=p-vec3(-3.3-open*.6,-.6,-1.45);
 stone=min(stone,rock(r,vec3(1.1,2.3,.7),-.7));
 r=p-vec3(2.8+open*.4,-.15,-1.7);
 stone=min(stone,rock(r,vec3(.9,2.5,.8),.6));
 r=p-vec3(.85,-1.25,-1.2);stone=min(stone,rock(r,vec3(1.8,.55,1.1),.6));
 if(stone<result.x)result=vec2(stone,2.);
 return result;
}
vec3 env(vec3 d){
 vec3 col=mix(vec3(.003,.009,.016),vec3(.055,.11,.18),smoothstep(-.3,.8,d.y));
 float soft=pow(max(0.,dot(d,normalize(vec3(-.5,.75,1.)))),16.);
 float strip=pow(max(0.,1.-abs(d.x*.75+d.y*.15+d.z*.45-.32)),65.);
 col+=vec3(.63,.87,1.)*soft*2.3+vec3(.5,.8,1.)*strip*.7;
 float panel=(1.-smoothstep(.12,.23,abs(d.x+.35)))*(1.-smoothstep(.44,.6,abs(d.y-.35)))*smoothstep(.1,.3,d.z);
 col+=vec3(.64,.79,.86)*panel*.7;
 col+=vec3(.08,.27,.6)*pow(max(0.,dot(d,normalize(vec3(.9,.1,-.6)))),12.);
 return col;
}
vec3 normalAt(vec3 p){
 vec2 e=vec2(1.,-1.)*.002;
 return normalize(e.xyy*scene(p+e.xyy).x+e.yyx*scene(p+e.yyx).x+e.yxy*scene(p+e.yxy).x+e.xxx*scene(p+e.xxx).x);
}
vec3 orbMaterial(vec3 n,vec3 rd){
 vec3 ref=reflect(rd,n);
 float fresnel=pow(1.-max(dot(n,-rd),0.),4.);
 vec4 w=chapters();
 float flow=.5+.5*sin(ref.x*2.4+ref.y*1.6+time*.13);
 vec3 spectrum=mix(glowBlue(),glowViolet(),flow);
 vec3 col=mix(env(ref)*.92,spectrum+env(ref)*.4,.89);
 // The same glass surface becomes an aurora, a focused eclipse, then a warm dawn.
 float aurora=pow(.5+.5*sin(n.y*7.+n.x*3.+time*.36),9.);
 col+=glowViolet()*aurora*w.y*.26;
 float lens=pow(max(0.,1.-abs(dot(n,normalize(vec3(.5,.15,1.))))),3.);
 col=mix(col,env(ref)*.42+glowBlue()*.045+glowViolet()*lens*.9,w.z*.9);
 float pulse=pow(max(0.,1.-abs(n.y-.18*sin(time*.5))),24.);
 col+=glowViolet()*pulse*w.z*.12;
 col+=vec3(.62,.26,.085)*pow(max(n.y,0.),4.)*w.w*.22;
 return col+lightColor()*fresnel*.43+vec3(.001,.008,.018);
}
vec3 shade(vec3 p,vec3 rd,float mat){
 vec3 n=normalAt(p),ref=reflect(rd,n);
 float fresnel=pow(1.-max(dot(n,-rd),0.),4.);
 vec3 col=env(ref);
 if(mat<.5){
   col*=.22;
   vec3 c=orbCenter();
   vec3 oc=p-c;float b=dot(oc,ref),radius=mix(.72,.57,max(0.,mode-1.));
   float d=b*b-dot(oc,oc)+radius*radius;
   if(d>0.&&b<0.){
     vec3 sn=normalize(p+ref*(-b-sqrt(d))-c);
     col+=orbMaterial(sn,ref)*.48*smoothstep(0.,.018,d);
   }
   float shadow=exp(-dot(p.xz,p.xz)*1.7)*.3;
   col*=1.-shadow;
   float grid=pow(.5+.5*sin(p.z*18.+.08*sin(p.x*8.)),32.);
   col+=vec3(.007,.015,.026)*grid*exp(-length(p.xz)*.2);
   col+=lightColor()*.023*exp(-dot(p.xz,p.xz)*.6);
 }else if(mat<1.5){col=orbMaterial(n,rd);}
 else if(mat<2.5){
   col=mix(col,env(normalize(ref+n*.18)),.45)*.72;
   float ao=clamp(scene(p+n*.16).x/.16,.35,1.);
   col*=ao;col+=vec3(.04,.1,.17)*fresnel;
 }else{
   float emissive=dot(chapters(),vec4(.03,.3,.65,.18));
   vec3 tint=mat>3.5?normalize(glowViolet()):lightColor();
   col=vec3(.014,.025,.045)+env(ref)*1.35+tint*(fresnel*.3+emissive);
 }
 return col;
}
vec3 signals(vec3 ro,vec3 rd,float sceneDepth){
 vec3 light=vec3(0.);float gather=ease(.15,1.05,progress),resolve=ease(1.15,2.2,progress);
 for(int i=0;i<9;i++){
  float f=float(i),angle=f*2.399+time*.13;
  vec3 loose=vec3(sin(f*3.9)*2.,cos(f*2.1)*1.2+.4,cos(f*3.3)*.8-.15);
  loose.y+=sin(time*.3+f)*.1;
  vec3 orbit=vec3(cos(angle)*1.13,sin(angle)*.42+.2,sin(angle)*.9);
  vec3 at=mix(loose,orbit,gather);at=mix(at,orbCenter(),resolve);
  vec3 rel=at-ro;float t=dot(rel,rd);float d=length(rel-rd*t);
  float energy=exp(-d*d*1400.)*.6+exp(-d*d*100.)*.028;
  float visible=smoothstep(-.04,.04,sceneDepth-t);
  light+=lightColor()*energy*visible*(1.-resolve);
 }
 return light;
}
vec3 atmosphere(vec3 ro,vec3 rd,float sceneDepth){
 vec3 rel=orbCenter()-ro;float along=dot(rel,rd),distance=length(rel-rd*along);
 float radius=mix(.72,.57,max(0.,mode-1.));
 float edge=max(0.,distance-radius);
 float halo=(exp(-edge*edge*16.)*.085+exp(-edge*edge*160.)*.055)*smoothstep(radius-.05,radius+.07,distance);
 float visible=smoothstep(-.12,.12,sceneDepth-along);
 vec3 light=lightColor()*halo*visible;
 vec4 w=chapters();
 // Analytic light ribbons share the solid rings' transforms and respect depth.
 for(int i=0;i<2;i++){
   float second=float(i),strength=second>.5?w.y+w.w:1.;
   vec3 o=ringSpace(ro-orbCenter(),second),d=ringSpace(rd,second);
   if(abs(d.y)>.001&&strength>.002){
     float t=-o.y/d.y;
     vec3 q=o+d*t;
     float r=second>.5?mix(.73,1.18,strength):ringRadius();
     float band=abs(length(q.xz)-r);
     float angle=atan(q.z,q.x);
     float sweep=.55+.45*sin(angle*2.-time*.65+second*2.);
     float ribbon=exp(-band*band*800.)*.19+exp(-band*band*70.)*.022;
     vec3 tint=second>.5?normalize(glowViolet()):lightColor();
     float exposure=dot(w,vec4(.15,.9,.9,.8));
     light+=tint*ribbon*sweep*strength*exposure*smoothstep(-.035,.06,sceneDepth-t)*step(0.,t);
   }
 }
 return light;
}
void main(){
 vec2 uv=(gl_FragCoord.xy*2.-resolution)/resolution.y;
 float aspect=resolution.x/resolution.y;
 uv.x-=aspect>1.12?.63:0.;uv.y+=aspect>1.12?-.05:.38;
 float dolly=sin(progress*1.15)*.68;
 vec3 ro=vec3(.22*sin(progress*1.4)+pointer.x*.08,.13+sin(progress*1.5)*.22,(aspect>1.12?4.9:7.2)-dolly);
 vec3 target=vec3(0.,.02,0.);
 vec3 f=normalize(target-ro),right=normalize(cross(f,vec3(0.,1.,0.))),up=cross(right,f);
 vec3 rd=normalize(right*uv.x+up*uv.y+f*2.85);
 float t=.3,mat=-1.;
 for(int i=0;i<78;i++){vec3 p=ro+rd*t;vec2 hit=scene(p);if(hit.x<.0035){mat=hit.y;break;}t+=hit.x*.72;if(t>14.)break;}
 vec3 col=vec3(.007,.014,.024)+vec3(.015,.036,.055)*exp(-length(uv-vec2(.1,.1))*1.4);
 if(mat>=0.){vec3 p=ro+rd*t;vec3 surface=shade(p,rd,mat);float fog=exp(-.014*t*t);col=mix(col,surface,fog);}
 col+=signals(ro,rd,mat>=0.?t:20.);
 col+=atmosphere(ro,rd,mat>=0.?t:20.);
 col=col/(col+vec3(.64));col=pow(max(col,vec3(0.)),vec3(.87));
 float vignette=1.-smoothstep(.5,2.2,length((gl_FragCoord.xy/resolution-.5)*vec2(1.,.75)))*.35;
 gl_FragColor=vec4(col*vignette,1.);
}`;

const finishFragment = `
precision mediump float;
uniform sampler2D sceneTexture;
uniform vec2 inverseSize;
uniform vec2 displaySize;
void main(){
 vec2 uv=gl_FragCoord.xy/displaySize;
 vec3 c=texture2D(sceneTexture,uv).rgb;
 vec3 nw=texture2D(sceneTexture,uv+vec2(-1.,1.)*inverseSize).rgb;
 vec3 ne=texture2D(sceneTexture,uv+vec2(1.,1.)*inverseSize).rgb;
 vec3 sw=texture2D(sceneTexture,uv+vec2(-1.,-1.)*inverseSize).rgb;
 vec3 se=texture2D(sceneTexture,uv+vec2(1.,-1.)*inverseSize).rgb;
 vec3 luma=vec3(.299,.587,.114);
 float a=dot(nw,luma),b=dot(ne,luma),d=dot(sw,luma),e=dot(se,luma),m=dot(c,luma);
 vec2 dir=vec2(-(a+b-d-e),a+d-b-e);
 float reduce=max((a+b+d+e)*.03125,.0078125);
 dir=clamp(dir/(min(abs(dir.x),abs(dir.y))+reduce),vec2(-4.),vec2(4.))*inverseSize;
 vec3 blend=.5*(texture2D(sceneTexture,uv+dir*(-1./6.)).rgb+texture2D(sceneTexture,uv+dir*(1./6.)).rgb);
 float contrast=max(max(a,b),max(d,e))-min(min(a,b),min(d,e));
 gl_FragColor=vec4(mix(c,blend,smoothstep(.018,.12,contrast)),1.);
}`;

export function ImmersiveScene({
  paused,
  mode,
  progress,
}: {
  paused: boolean;
  mode: number;
  progress: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const settings = useRef({ paused, mode, progress });
  const wake = useRef<() => void>(() => {});
  const [available, setAvailable] = useState(false);
  useEffect(() => {
    settings.current = { paused, mode, progress };
    wake.current();
  }, [paused, mode, progress]);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const gl = canvas.getContext("webgl", {
      alpha: false,
      antialias: false,
      powerPreference: "low-power",
    });
    if (!gl) return;
    const shaders: WebGLShader[] = [];
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      shaders.push(shader);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
    };
    const vertexShader = compile(gl.VERTEX_SHADER, vertex);
    const link = (source: string) => {
      const shader = compile(gl.FRAGMENT_SHADER, source),
        program = gl.createProgram();
      if (!shader || !vertexShader || !program) {
        if (program) gl.deleteProgram(program);
        return null;
      }
      gl.attachShader(program, vertexShader);
      gl.attachShader(program, shader);
      gl.bindAttribLocation(program, 0, "position");
      gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        gl.deleteProgram(program);
        return null;
      }
      return program;
    };
    const sceneProgram = link(fragment),
      finishProgram = link(finishFragment);
    if (!sceneProgram || !finishProgram) {
      if (sceneProgram) gl.deleteProgram(sceneProgram);
      if (finishProgram) gl.deleteProgram(finishProgram);
      for (const shader of shaders) gl.deleteShader(shader);
      return;
    }
    const activate = gl.useProgram.bind(gl);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const loc = Object.fromEntries(
      ["resolution", "pointer", "time", "mode", "progress"].map((k) => [
        k,
        gl.getUniformLocation(sceneProgram, k),
      ]),
    );
    const finishLoc = Object.fromEntries(
      ["sceneTexture", "inverseSize", "displaySize"].map((k) => [
        k,
        gl.getUniformLocation(finishProgram, k),
      ]),
    );
    const texture = gl.createTexture(),
      framebuffer = gl.createFramebuffer();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const tiers = [
      { width: 860, height: 700, ratio: 0.9 },
      { width: 1280, height: 1000, ratio: 1.25 },
      { width: 1600, height: 1200, ratio: 1.5 },
    ];
    let quality = (navigator.hardwareConcurrency || 8) <= 4 ? 0 : 1;
    let width = 1,
      height = 1,
      frame = 0,
      last = 0,
      elapsed = 0,
      x = 0,
      y = 0,
      px = 0,
      py = 0,
      visible = true,
      lost = false;
    let renderedMode = settings.current.mode,
      renderedProgress = settings.current.progress;
    let sampleSum = 0,
      sampleCount = 0,
      slowWindows = 0,
      fastWindows = 0,
      lastQualityChange = 0;
    const resetSamples = () => {
      sampleSum = 0;
      sampleCount = 0;
      slowWindows = 0;
      fastWindows = 0;
    };
    const allocate = () => {
      const rect = canvas.getBoundingClientRect();
      const tier = tiers[quality];
      const ratio = Math.min(
        devicePixelRatio || 1,
        tier.ratio,
        tier.width / Math.max(rect.width, 1),
        tier.height / Math.max(rect.height, 1),
      );
      width = Math.max(1, Math.round(rect.width * ratio));
      height = Math.max(1, Math.round(rect.height * ratio));
      const outputRatio = Math.min(
        devicePixelRatio || 1,
        1.5,
        1800 / Math.max(rect.width, 1),
        1350 / Math.max(rect.height, 1),
      );
      canvas.width = Math.max(1, Math.round(rect.width * outputRatio));
      canvas.height = Math.max(1, Math.round(rect.height * outputRatio));
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        width,
        height,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        null,
      );
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.framebufferTexture2D(
        gl.FRAMEBUFFER,
        gl.COLOR_ATTACHMENT0,
        gl.TEXTURE_2D,
        texture,
        0,
      );
      if (
        gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE
      ) {
        lost = true;
        setAvailable(false);
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      canvas.dataset.quality = ["economy", "balanced", "high"][quality];
      canvas.dataset.renderWidth = String(width);
      canvas.dataset.renderHeight = String(height);
    };
    const draw = () => {
      if (lost) return;
      activate(sceneProgram);
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.viewport(0, 0, width, height);
      gl.uniform2f(loc.resolution, width, height);
      gl.uniform2f(loc.pointer, x, y);
      gl.uniform1f(loc.time, elapsed);
      gl.uniform1f(loc.mode, renderedMode);
      gl.uniform1f(loc.progress, renderedProgress);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
      activate(finishProgram);
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.uniform1i(finishLoc.sceneTexture, 0);
      gl.uniform2f(finishLoc.inverseSize, 1 / width, 1 / height);
      gl.uniform2f(finishLoc.displaySize, canvas.width, canvas.height);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    };
    const tick = (now: number) => {
      frame = 0;
      if (!visible || document.hidden || lost) return;
      const still = settings.current.paused || media.matches;
      if (now - last >= 32) {
        const delta = last ? now - last : 33;
        if (!still) {
          elapsed += Math.min(delta / 1000, 0.075);
          x += (px - x) * 0.08;
          y += (py - y) * 0.08;
          renderedMode += (settings.current.mode - renderedMode) * 0.14;
          renderedProgress +=
            (settings.current.progress - renderedProgress) * 0.2;
          // Measure only visible, uninterrupted rendering; hysteresis prevents quality flicker.
          if (last) {
            sampleSum += Math.min(delta, 200);
            sampleCount++;
          }
          if (sampleCount >= 40) {
            const average = sampleSum / sampleCount;
            slowWindows = average > 54 ? slowWindows + 1 : 0;
            fastWindows = average < 37 ? fastWindows + 1 : 0;
            sampleSum = 0;
            sampleCount = 0;
            const next =
              slowWindows >= 2
                ? Math.max(0, quality - 1)
                : fastWindows >= 6
                  ? Math.min(2, quality + 1)
                  : quality;
            if (next !== quality && now - lastQualityChange > 5000) {
              quality = next;
              lastQualityChange = now;
              allocate();
              resetSamples();
            }
          }
        } else {
          renderedMode = settings.current.mode;
          renderedProgress = settings.current.progress;
        }
        last = now;
        draw();
      }
      if (!still) frame = requestAnimationFrame(tick);
    };
    const sync = () => {
      if (!visible || document.hidden || lost) {
        cancelAnimationFrame(frame);
        frame = 0;
        last = 0;
        resetSamples();
        return;
      }
      if (settings.current.paused || media.matches) {
        cancelAnimationFrame(frame);
        frame = 0;
        last = 0;
        resetSamples();
        renderedMode = settings.current.mode;
        renderedProgress = settings.current.progress;
        draw();
      } else if (!frame) {
        last = 0;
        frame = requestAnimationFrame(tick);
      }
    };
    const resize = () => {
      allocate();
      resetSamples();
      sync();
    };
    const pointer = (event: PointerEvent) => {
      if (event.pointerType === "touch") return;
      px = (event.clientX / innerWidth - 0.5) * 2;
      py = (event.clientY / innerHeight - 0.5) * 2;
    };
    const contextLost = (event: Event) => {
      event.preventDefault();
      lost = true;
      cancelAnimationFrame(frame);
      frame = 0;
      setAvailable(false);
    };
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      sync();
    });
    observer.observe(canvas);
    const resizer = new ResizeObserver(resize);
    resizer.observe(canvas);
    addEventListener("pointermove", pointer, { passive: true });
    document.addEventListener("visibilitychange", sync);
    media.addEventListener("change", sync);
    canvas.addEventListener("webglcontextlost", contextLost);
    wake.current = sync;
    resize();
    if (!lost) setAvailable(true);
    return () => {
      wake.current = () => {};
      cancelAnimationFrame(frame);
      observer.disconnect();
      resizer.disconnect();
      removeEventListener("pointermove", pointer);
      document.removeEventListener("visibilitychange", sync);
      media.removeEventListener("change", sync);
      canvas.removeEventListener("webglcontextlost", contextLost);
      gl.deleteTexture(texture);
      gl.deleteFramebuffer(framebuffer);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(sceneProgram);
      gl.deleteProgram(finishProgram);
      for (const shader of shaders) gl.deleteShader(shader);
    };
  }, []);
  return (
    <div
      className={styles.world}
      data-renderer={available ? "webgl" : "fallback"}
    >
      <canvas
        className={styles.canvas}
        ref={canvasRef}
        aria-hidden="true"
        tabIndex={-1}
      />
    </div>
  );
}
