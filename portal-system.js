document.addEventListener('DOMContentLoaded',()=>{
document.querySelectorAll('canvas').forEach(canvas=>{
 const wrapper=canvas.parentElement;if(!wrapper)return;
 const tb=document.createElement('div');tb.className='chart-toolbar';
 tb.innerHTML='<button class="expand-btn">⤢ Expand</button><button class="csv-btn">CSV</button><button class="png-btn">PNG</button>';
 wrapper.prepend(tb);
 tb.querySelector('.expand-btn').onclick=()=>openFullscreen(canvas);
 tb.querySelector('.png-btn').onclick=()=>{
   if(canvas.toDataURL){const a=document.createElement('a');a.download='chart.png';a.href=canvas.toDataURL();a.click();}
 };
});
});
function openFullscreen(canvas){
 const modal=document.createElement('div');
 modal.className='fullscreen-chart';
 modal.innerHTML='<button id="closeFull">Close</button>';
 modal.appendChild(canvas.cloneNode(true));
 document.body.appendChild(modal);
 document.getElementById('closeFull').onclick=()=>modal.remove();
}
